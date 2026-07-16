"""Runtime-настройки: редактируемый через UI операционный конфиг.

Эффективное значение = запись в app_setting (БД) поверх дефолта из .env.
Позволяет менять параметры AD из интерфейса без правки .env и рестарта:
после сохранения кэш перечитывается, а AD-коннектор переподключается.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.modules.config import settings

logger = logging.getLogger("log_analyzer")


@dataclass(frozen=True)
class ADConfig:
    server: str
    domain: str
    username: str
    password: str
    use_ssl: bool
    start_tls: bool
    auth: str
    port: Optional[int]
    tls_validate: bool
    upn_suffix: str
    default_user_ou: str
    login_group: str
    connect_timeout: Optional[int] = None
    use_pooling: bool = False
    display_name: str = "Active Directory"
    vendor: str = "ad"
    # --- Многопровайдерный режим (в стиле Avanpost) ---
    # Список адресов серверов (primary + резервные), напр. ldaps://host:636.
    servers: tuple[str, ...] = ()
    base_dn: str = ""              # база поиска; пусто = defaultNamingContext
    user_filter: str = ""          # доп. LDAP-фильтр пользователей
    group_filter: str = ""         # доп. LDAP-фильтр групп
    attr_login: str = "sAMAccountName"
    attr_email: str = "mail"
    attr_display: str = "displayName"
    attr_first: str = "givenName"
    attr_last: str = "sn"


def build_connection_url(server: str, port: Optional[int], use_ssl: bool) -> str:
    """server/port/ssl -> ldap(s)://host:port для отображения в UI."""
    host = str(server or "").strip()
    if not host:
        return ""
    scheme = "ldaps" if use_ssl else "ldap"
    p = port or (636 if use_ssl else 389)
    return f"{scheme}://{host}:{p}"


def parse_connection_url(url: str) -> Optional[tuple[str, int, bool]]:
    """ldap(s)://host:port -> (host, port, use_ssl). None если пусто/битое."""
    u = str(url or "").strip()
    if not u:
        return None
    use_ssl = u.lower().startswith("ldaps://")
    rest = u.split("://", 1)[1] if "://" in u else u
    rest = rest.strip("/ ")
    host, _, port_s = rest.partition(":")
    host = host.strip()
    if not host:
        return None
    port = int(port_s) if port_s.strip().isdigit() else (636 if use_ssl else 389)
    return host, port, use_ssl


# Редактируемые ключи: имя -> (тип, функция-дефолт из .env).
# Типы: str | bool | int_opt (int или None) | secret (str, но в UI не отдаётся).
_AD_FIELDS: dict[str, tuple[str, Callable[[], Any]]] = {
    "ad_server": ("str", lambda: settings.ad_server),
    "ad_domain": ("str", lambda: settings.ad_domain),
    "ad_username": ("str", lambda: settings.ad_username),
    "ad_password": ("secret", lambda: settings.ad_password),
    "ad_use_ssl": ("bool", lambda: settings.ad_use_ssl),
    "ad_start_tls": ("bool", lambda: settings.ad_start_tls),
    "ad_auth": ("str", lambda: settings.ad_auth),
    "ad_port": ("int_opt", lambda: settings.ad_port),
    "ad_tls_validate": ("bool", lambda: settings.ad_tls_validate),
    "ad_upn_suffix": ("str", lambda: settings.ad_upn_suffix),
    "ad_default_user_ou": ("str", lambda: settings.ad_default_user_ou),
    "ad_login_group": ("str", lambda: settings.ad_login_group),
    # --- Дополнительные поля LDAP-провайдера (в стиле Keycloak) ---
    "ldap_display_name": ("str", lambda: "Active Directory"),
    "ldap_vendor": ("str", lambda: "ad"),
    "ldap_use_pooling": ("bool", lambda: False),
    "ldap_connect_timeout": ("int_opt", lambda: None),
}


def _to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


def _to_int_opt(value: Any) -> Optional[int]:
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _store_value(key: str, raw: Any) -> str:
    typ = _AD_FIELDS[key][0]
    if typ == "bool":
        return "1" if _to_bool(raw) else "0"
    if typ == "int_opt":
        v = _to_int_opt(raw)
        return "" if v is None else str(v)
    return "" if raw is None else str(raw)


class RuntimeSettings:
    def __init__(self) -> None:
        # только реально переопределённые ключи (то, что лежит в app_setting)
        self._overrides: dict[str, str] = {}

    async def load(self) -> None:
        try:
            from backend.modules.app_db import app_db

            if not app_db.ready:
                logger.info("Runtime settings: app DB не готова, использую .env")
                return
            self._overrides = await app_db.get_all_settings()
            logger.info("Runtime settings: загружено переопределений: %s", len(self._overrides))
        except Exception:
            logger.exception("Runtime settings: ошибка загрузки, использую .env")

    def _effective(self, key: str) -> Any:
        typ, default = _AD_FIELDS[key]
        if key in self._overrides:
            raw = self._overrides[key]
            if typ == "bool":
                return _to_bool(raw)
            if typ == "int_opt":
                return _to_int_opt(raw)
            return str(raw)
        return default()

    def get_ad_config(self) -> ADConfig:
        # Приоритет — активный LDAP-провайдер (многопровайдерный режим).
        # Если провайдеров нет, откатываемся на плоские ad_*-ключи поверх .env.
        try:
            from backend.modules.ldap_providers import ldap_providers

            active = ldap_providers.active_ad_config()
            if active is not None:
                return active
        except Exception:
            logger.exception("LDAP providers: активный конфиг недоступен")

        return self._env_ad_config()

    def _env_ad_config(self) -> ADConfig:
        return ADConfig(
            server=self._effective("ad_server"),
            domain=self._effective("ad_domain"),
            username=self._effective("ad_username"),
            password=self._effective("ad_password"),
            use_ssl=bool(self._effective("ad_use_ssl")),
            start_tls=bool(self._effective("ad_start_tls")),
            auth=self._effective("ad_auth"),
            port=self._effective("ad_port"),
            tls_validate=bool(self._effective("ad_tls_validate")),
            upn_suffix=self._effective("ad_upn_suffix"),
            default_user_ou=self._effective("ad_default_user_ou"),
            login_group=self._effective("ad_login_group"),
            connect_timeout=self._effective("ldap_connect_timeout"),
            use_pooling=bool(self._effective("ldap_use_pooling")),
            display_name=self._effective("ldap_display_name"),
            vendor=self._effective("ldap_vendor"),
        )

    def public_ad(self) -> dict[str, Any]:
        """Значения для UI. Пароль сервисной учётки не отдаём — только факт."""
        cfg = self.get_ad_config()
        return {
            "ad_server": cfg.server,
            "ad_domain": cfg.domain,
            "ad_username": cfg.username,
            "ad_password_set": bool(cfg.password),
            "ad_use_ssl": cfg.use_ssl,
            "ad_start_tls": cfg.start_tls,
            "ad_auth": cfg.auth,
            "ad_port": cfg.port,
            "ad_tls_validate": cfg.tls_validate,
            "ad_upn_suffix": cfg.upn_suffix,
            "ad_default_user_ou": cfg.default_user_ou,
            "ad_login_group": cfg.login_group,
        }

    async def update_ad(self, values: dict[str, Any]) -> None:
        to_write: dict[str, str] = {}
        for key, raw in values.items():
            if key not in _AD_FIELDS:
                continue
            # Пустой секрет = «не менять», не затираем существующий пароль.
            if _AD_FIELDS[key][0] == "secret" and (raw is None or str(raw) == ""):
                continue
            to_write[key] = _store_value(key, raw)
        if not to_write:
            return
        from backend.modules.app_db import app_db

        await app_db.set_settings(to_write)
        self._overrides.update(to_write)
        logger.info("Runtime settings: обновлено ключей: %s", ", ".join(to_write))

    # ------------------------------------------------------------------
    # LDAP-провайдер (форма в стиле Keycloak). Ложится на те же ad_*-ключи —
    # один источник правды, — плюс пары косметических ldap_*-настроек.
    # ------------------------------------------------------------------
    def public_ldap(self) -> dict[str, Any]:
        """Настройки провайдера в форме, удобной для UI-вкладки."""
        cfg = self.get_ad_config()
        return {
            "display_name": cfg.display_name,
            "vendor": cfg.vendor,
            "connection_url": build_connection_url(
                cfg.server, cfg.port, cfg.use_ssl
            ),
            "start_tls": cfg.start_tls,
            "validate_cert": cfg.tls_validate,
            "use_pooling": cfg.use_pooling,
            "connect_timeout": cfg.connect_timeout,
            "bind_type": cfg.auth,
            "bind_dn": cfg.username,
            "bind_credentials_set": bool(cfg.password),
        }

    async def update_ldap(self, values: dict[str, Any]) -> None:
        """Маппинг полей формы провайдера на внутренние ad_*/ldap_*-ключи."""
        mapped: dict[str, Any] = {}

        if "connection_url" in values:
            parsed = parse_connection_url(values.get("connection_url"))
            if parsed:
                host, port, use_ssl = parsed
                mapped["ad_server"] = host
                mapped["ad_port"] = port
                mapped["ad_use_ssl"] = use_ssl

        _pairs = {
            "start_tls": "ad_start_tls",
            "validate_cert": "ad_tls_validate",
            "bind_type": "ad_auth",
            "bind_dn": "ad_username",
            "use_pooling": "ldap_use_pooling",
            "connect_timeout": "ldap_connect_timeout",
            "display_name": "ldap_display_name",
            "vendor": "ldap_vendor",
        }
        for src, dst in _pairs.items():
            if src in values:
                mapped[dst] = values[src]

        # Пароль bind — пустой = «не менять» (обрабатывается в update_ad).
        if "bind_credentials" in values:
            mapped["ad_password"] = values["bind_credentials"]

        await self.update_ad(mapped)


runtime_settings = RuntimeSettings()


def get_ad_config() -> ADConfig:
    return runtime_settings.get_ad_config()
