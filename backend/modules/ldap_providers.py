"""Реестр LDAP-провайдеров (в стиле Avanpost).

Несколько источников LDAP/AD, каждый со своими адресами (с резервными),
Base DN, учёткой подключения, фильтрами поиска и сопоставлением атрибутов.
Ровно один провайдер помечен активным — он питает вход, анализатор и
AD-админку через `runtime_settings.get_ad_config()`.

Хранится в app_setting под ключом `ldap_providers` (JSON). Пароли bind лежат
в БД (как и у коллекторов); в UI не отдаются — только факт `bind_password_set`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import uuid4

from backend.modules.runtime_settings import (
    ADConfig,
    build_connection_url,
    parse_connection_url,
)

logger = logging.getLogger("log_analyzer")

SETTING_KEY = "ldap_providers"

_ATTR_DEFAULTS = {
    "attr_login": "sAMAccountName",
    "attr_email": "mail",
    "attr_display": "displayName",
    "attr_first": "givenName",
    "attr_last": "sn",
}


def _int_opt(value: Any) -> Optional[int]:
    s = str(value if value is not None else "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _servers_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(s).strip() for s in value if str(s).strip()]


def _split_host_port(entry: Any, default_port: int) -> Optional[tuple[str, int]]:
    """'host' | 'host:port' | 'ldaps://host:port' -> (host, port)."""
    s = str(entry or "").strip()
    if not s:
        return None
    if "://" in s:
        parsed = parse_connection_url(s)
        return (parsed[0], parsed[1]) if parsed else None
    host, _, port_s = s.partition(":")
    host = host.strip()
    if not host:
        return None
    port = int(port_s) if port_s.strip().isdigit() else default_port
    return host, port


def _provider_to_ad_config(p: dict) -> ADConfig:
    host = str(p.get("host") or "").strip()
    port = _int_opt(p.get("port"))
    use_ssl = bool(p.get("use_ssl"))

    # Миграция со старого формата servers=[ldaps://host:port].
    if not host:
        old = _servers_list(p.get("servers"))
        parsed = parse_connection_url(old[0]) if old else None
        if parsed:
            host, port, use_ssl = parsed

    default_port = port or (636 if use_ssl else 389)
    # servers-кортеж (основной + резервные) строим сами — коннектор
    # перебирает его при недоступности; ssl одинаковый для всех.
    urls: list[str] = []
    if host:
        urls.append(build_connection_url(host, default_port, use_ssl))
    for entry in _servers_list(p.get("failover")):
        hp = _split_host_port(entry, default_port)
        if hp:
            urls.append(build_connection_url(hp[0], hp[1], use_ssl))

    return ADConfig(
        server=host,
        port=port,
        use_ssl=use_ssl,
        domain=str(p.get("domain") or ""),
        username=str(p.get("bind_dn") or ""),
        password=str(p.get("bind_password") or ""),
        start_tls=bool(p.get("start_tls")),
        auth=str(p.get("bind_type") or "simple"),
        tls_validate=bool(p.get("tls_validate")),
        upn_suffix=str(p.get("upn_suffix") or ""),
        default_user_ou=str(p.get("default_user_ou") or ""),
        login_group=str(p.get("login_group") or ""),
        connect_timeout=_int_opt(p.get("connect_timeout")),
        use_pooling=bool(p.get("use_pooling")),
        display_name=str(p.get("name") or "LDAP"),
        vendor=str(p.get("vendor") or "ad"),
        servers=tuple(urls),
        base_dn=str(p.get("base_dn") or ""),
        user_filter=str(p.get("user_filter") or ""),
        group_filter=str(p.get("group_filter") or ""),
        attr_login=str(p.get("attr_login") or _ATTR_DEFAULTS["attr_login"]),
        attr_email=str(p.get("attr_email") or _ATTR_DEFAULTS["attr_email"]),
        attr_display=str(p.get("attr_display") or _ATTR_DEFAULTS["attr_display"]),
        attr_first=str(p.get("attr_first") or _ATTR_DEFAULTS["attr_first"]),
        attr_last=str(p.get("attr_last") or _ATTR_DEFAULTS["attr_last"]),
    )


def _seed_from(cfg: ADConfig) -> dict:
    """Первичный провайдер из текущего эффективного AD-конфига (.env/ad_*)."""
    return {
        "id": uuid4().hex,
        "name": cfg.display_name or "Active Directory",
        "enabled": True,
        "active": True,
        "vendor": cfg.vendor or "ad",
        "host": cfg.server or "",
        "port": cfg.port,
        "use_ssl": bool(cfg.use_ssl),
        "failover": [],
        "start_tls": bool(cfg.start_tls),
        "tls_validate": bool(cfg.tls_validate),
        "bind_type": cfg.auth or "simple",
        "bind_dn": cfg.username or "",
        "bind_password": cfg.password or "",
        "domain": cfg.domain or "",
        "connect_timeout": cfg.connect_timeout,
        "use_pooling": bool(cfg.use_pooling),
        "base_dn": "",
        "user_filter": "",
        "group_filter": "",
        "upn_suffix": cfg.upn_suffix or "",
        "default_user_ou": cfg.default_user_ou or "",
        "login_group": cfg.login_group or "",
        **_ATTR_DEFAULTS,
    }


def _normalize(raw: dict, pid: str, old: dict) -> dict:
    # Пустой пароль bind = «не менять»: берём прежний по id.
    pwd = raw.get("bind_password")
    if pwd is None or str(pwd) == "":
        pwd = old.get("bind_password", "")
    prov = {
        "id": pid,
        "name": str(raw.get("name") or "").strip() or "LDAP",
        "enabled": bool(raw.get("enabled", True)),
        "active": bool(raw.get("active", False)),
        "vendor": str(raw.get("vendor") or "ad"),
        "host": str(raw.get("host") or "").strip(),
        "port": _int_opt(raw.get("port")),
        "use_ssl": bool(raw.get("use_ssl")),
        "failover": _servers_list(raw.get("failover")),
        "start_tls": bool(raw.get("start_tls")),
        "tls_validate": bool(raw.get("tls_validate")),
        "bind_type": str(raw.get("bind_type") or "simple"),
        "bind_dn": str(raw.get("bind_dn") or ""),
        "bind_password": str(pwd or ""),
        "domain": str(raw.get("domain") or ""),
        "connect_timeout": _int_opt(raw.get("connect_timeout")),
        "use_pooling": bool(raw.get("use_pooling")),
        "base_dn": str(raw.get("base_dn") or ""),
        "user_filter": str(raw.get("user_filter") or ""),
        "group_filter": str(raw.get("group_filter") or ""),
        "upn_suffix": str(raw.get("upn_suffix") or ""),
        "default_user_ou": str(raw.get("default_user_ou") or ""),
        "login_group": str(raw.get("login_group") or ""),
    }
    for key, default in _ATTR_DEFAULTS.items():
        prov[key] = str(raw.get(key) or default)
    return prov


def _ensure_single_active(providers: list[dict]) -> None:
    """Ровно один активный среди включённых (или ни одного, если пусто)."""
    enabled = [p for p in providers if p.get("enabled")]
    for p in providers:
        if not p.get("enabled"):
            p["active"] = False
    chosen = next((p for p in enabled if p.get("active")), None)
    if chosen is None and enabled:
        chosen = enabled[0]
    for p in providers:
        p["active"] = p is chosen


class LdapProviders:
    def __init__(self) -> None:
        self._providers: list[dict] = []

    async def load(self) -> None:
        try:
            from backend.modules.app_db import app_db

            if not app_db.ready:
                return
            raw = (await app_db.get_setting(SETTING_KEY, "")).strip()
            if raw:
                data = json.loads(raw)
                items = data.get("providers") if isinstance(data, dict) else data
                if isinstance(items, list) and items:
                    self._providers = items
                    _ensure_single_active(self._providers)
                    logger.info("LDAP providers: загружено %s", len(items))
                    return

            # Пусто — сеем один провайдер из текущего .env/ad_* конфига.
            from backend.modules.runtime_settings import runtime_settings

            seed = _seed_from(runtime_settings._env_ad_config())
            self._providers = [seed]
            _ensure_single_active(self._providers)
            await self._persist()
            logger.info("LDAP providers: создан провайдер по умолчанию из .env")
        except Exception:
            logger.exception("LDAP providers: ошибка загрузки")

    async def _persist(self) -> None:
        from backend.modules.app_db import app_db

        payload = json.dumps({"providers": self._providers}, ensure_ascii=False)
        await app_db.set_setting(SETTING_KEY, payload)

    def active_ad_config(self) -> Optional[ADConfig]:
        for p in self._providers:
            if p.get("active") and p.get("enabled"):
                return _provider_to_ad_config(p)
        return None

    def public(self) -> list[dict]:
        out: list[dict] = []
        for p in self._providers:
            item = {k: v for k, v in p.items() if k != "bind_password"}
            item["bind_password_set"] = bool(p.get("bind_password"))
            out.append(item)
        return out

    def get_by_id(self, pid: str) -> Optional[dict]:
        return next((p for p in self._providers if p.get("id") == pid), None)

    async def save_all(self, incoming: list[dict]) -> None:
        old_by_id = {p.get("id"): p for p in self._providers}
        result: list[dict] = []
        seen: set[str] = set()
        for raw in incoming or []:
            pid = str(raw.get("id") or "").strip() or uuid4().hex
            while pid in seen:
                pid = uuid4().hex
            seen.add(pid)
            result.append(_normalize(raw, pid, old_by_id.get(pid, {})))
        _ensure_single_active(result)
        self._providers = result
        await self._persist()
        logger.info("LDAP providers: сохранено %s", len(result))


ldap_providers = LdapProviders()
