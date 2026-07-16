from __future__ import annotations

import logging
import ssl
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ldap3 import (
    ALL,
    ANONYMOUS,
    AUTO_BIND_TLS_BEFORE_BIND,
    BASE,
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
    NTLM,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.core.exceptions import LDAPSocketSendError
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn, safe_rdn

from backend.modules.runtime_settings import get_ad_config, parse_connection_url

logger = logging.getLogger(__name__)


# userAccountControl flags
UAC_NORMAL_ACCOUNT = 0x0200  # 512
UAC_ACCOUNTDISABLE = 0x0002  # 2
UAC_DONT_EXPIRE_PASSWD = 0x10000  # 65536

# accountExpires: FILETIME (100-нс интервалы с 1601-01-01).
FILETIME_EPOCH = 11644473600  # секунд между 1601-01-01 и 1970-01-01
FILETIME_NEVER = 0x7FFFFFFFFFFFFFFF  # «никогда не истекает»
NEVER_KEYWORDS = {"never", "бессрочно", "нет", "no", "-", "0", ""}


class ADWriteError(Exception):
    """Ошибка операции записи в AD с человеко-читаемым описанием."""


def _parse_input_date(value: str) -> Optional[datetime]:
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def date_to_account_expires(value: str) -> str:
    """'активна до' (дата) -> строка accountExpires (FILETIME). '0' = никогда."""
    s = (value or "").strip().lower()
    if s in NEVER_KEYWORDS:
        return "0"
    d = _parse_input_date(value)
    if not d:
        raise ADWriteError(f"Неверная дата срока действия: {value!r}")
    # Учётка активна включительно по указанный день => истекает в начале
    # следующего дня (как «End of <date>» в оснастке AD).
    expiry = datetime(d.year, d.month, d.day) + timedelta(days=1)
    ts = expiry.timestamp()  # локальная зона сервера
    return str(int(round((ts + FILETIME_EPOCH) * 10_000_000)))


def account_expires_to_iso(value) -> str:
    """accountExpires (AD) -> 'YYYY-MM-DD' (день, по который активна). '' = никогда."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return ""

    if isinstance(value, datetime):
        if value.year >= 9999 or value.year <= 1601:
            return ""
        v = value.astimezone() if value.tzinfo else value
        return (v - timedelta(seconds=1)).strftime("%Y-%m-%d")

    s = str(value).strip()
    if not s:
        return ""
    if s.lstrip("-").isdigit():
        ft = int(s)
        if ft <= 0 or ft >= FILETIME_NEVER:
            return ""
        ts = ft / 10_000_000 - FILETIME_EPOCH
        try:
            v = datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return ""
        return (v - timedelta(seconds=1)).strftime("%Y-%m-%d")
    try:
        v = datetime.fromisoformat(s)
    except ValueError:
        return ""
    if v.year >= 9999:
        return ""
    v = v.astimezone() if v.tzinfo else v
    return (v - timedelta(seconds=1)).strftime("%Y-%m-%d")


def format_when(value) -> str:
    """whenCreated (AD) -> 'DD.MM.YYYY HH:MM' в локальной зоне."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return ""
    if isinstance(value, datetime):
        v = value.astimezone() if value.tzinfo else value
        return v.strftime("%d.%m.%Y %H:%M")
    s = str(value).strip()
    if len(s) >= 14 and s[:14].isdigit():
        try:
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S").strftime(
                "%d.%m.%Y %H:%M"
            )
        except ValueError:
            pass
    return s


class ADConnector:
    def __init__(self, bind_as: Optional[tuple[str, str, str]] = None):
        # Эффективный конфиг: настройки из UI (БД) поверх дефолтов .env.
        # bind_as=(username, password, auth) — делегирование: подключаемся под
        # кредами вошедшего пользователя вместо служебной учётки.
        cfg = get_ad_config()
        self.server_name = cfg.server
        self.domain = cfg.domain
        self.username = cfg.username
        self.password = cfg.password

        self.use_ssl = bool(cfg.use_ssl)
        self.start_tls = bool(cfg.start_tls)
        self.bind_type = str(cfg.auth or "simple").strip().lower()

        if bind_as is not None:
            self.username = str(bind_as[0] or "")
            self.password = str(bind_as[1] or "")
            self.bind_type = str(bind_as[2] or "ntlm").strip().lower()
            self.delegated = True
        else:
            self.delegated = False
        self.use_ntlm = self.bind_type == "ntlm"
        self.anonymous = self.bind_type == "anonymous"
        # Если порт не задан явно — LDAPS=636, иначе 389.
        self.port = cfg.port or (636 if self.use_ssl else 389)
        self.tls_validate = bool(cfg.tls_validate)
        self.upn_suffix = str(cfg.upn_suffix or "").strip()
        self.default_user_ou = str(cfg.default_user_ou or "").strip()
        # Таймаут TCP-подключения к контроллеру домена (сек), None = ldap3-дефолт.
        self.connect_timeout = cfg.connect_timeout

        # Многопровайдерные поля (Avanpost): резервные серверы, база поиска,
        # фильтры и сопоставление атрибутов. Дефолты совпадают с AD — для AD
        # поведение не меняется.
        self.servers = list(getattr(cfg, "servers", ()) or [])
        self.base_dn_override = str(getattr(cfg, "base_dn", "") or "").strip()
        self.user_filter = str(getattr(cfg, "user_filter", "") or "").strip()
        self.group_filter = str(getattr(cfg, "group_filter", "") or "").strip()
        self.attr_login = str(getattr(cfg, "attr_login", "") or "sAMAccountName")
        self.attr_email = str(getattr(cfg, "attr_email", "") or "mail")
        self.attr_display = str(getattr(cfg, "attr_display", "") or "displayName")
        self.attr_first = str(getattr(cfg, "attr_first", "") or "givenName")
        self.attr_last = str(getattr(cfg, "attr_last", "") or "sn")

        self.connection: Optional[Connection] = None
        self.base_dn: Optional[str] = None
        # Защищаем разделяемое соединение от одновременных операций записи.
        self._write_lock = threading.Lock()

    @property
    def secure(self) -> bool:
        """True, если канал зашифрован (LDAPS или StartTLS) — нужно для пароля."""
        return self.use_ssl or self.start_tls

    def _bind_user(self) -> str:
        """Имя для bind: полный логin (DN, UPN, DOMAIN\\user) — как есть,
        голый sAMAccountName — с подстановкой NetBIOS-домена."""
        user = str(self.username or "").strip()
        if not user:
            return user
        if "\\" in user or "@" in user or "=" in user:
            return user
        if self.domain:
            return f"{self.domain}\\{user}"
        return user

    def _ident_filter(self, ident: str) -> str:
        """LDAP-фильтр поиска пользователя по идентификатору.

        Использует настраиваемый атрибут логина (attr_login, по умолчанию
        sAMAccountName) и необязательный доп. фильтр провайдера (user_filter).
        Для AD с дефолтами эквивалентно (&(objectClass=user)(sAMAccountName=…)).
        """
        safe = escape_filter_chars((ident or "").strip())
        extra = self.user_filter or ""
        return f"(&(objectClass=user)({self.attr_login}={safe}){extra})"

    def _targets(self) -> list[tuple[str, int, bool]]:
        """Список (host, port, use_ssl): резервные серверы провайдера."""
        targets: list[tuple[str, int, bool]] = []
        for s in self.servers:
            parsed = parse_connection_url(s)
            if parsed:
                targets.append(parsed)
        if not targets:
            targets.append((self.server_name, self.port, self.use_ssl))
        return targets

    def connect(self) -> bool:
        want_start_tls = self.start_tls
        for host, port, use_ssl in self._targets():
            self.server_name = host
            self.port = port
            self.use_ssl = use_ssl
            self.start_tls = want_start_tls
            if self._connect_attempt():
                return True

            # Фолбэк: если просили зашифрованный канал и не удалось — пробуем
            # обычный LDAP (389) на этом же хосте, чтобы чтение продолжало
            # работать. Операции с паролем будут недоступны (secure=False).
            if use_ssl or want_start_tls:
                logger.warning(
                    "[AD] Secure connect to %s failed — пробую обычный LDAP (389)",
                    host,
                )
                self.use_ssl = False
                self.start_tls = False
                self.port = 389
                if self._connect_attempt():
                    return True

        return False

    def _connect_attempt(self) -> bool:
        try:
            tls = None
            if self.use_ssl or self.start_tls:
                validate = (
                    ssl.CERT_REQUIRED if self.tls_validate else ssl.CERT_NONE
                )
                tls = Tls(validate=validate)

            server_kwargs: dict = {
                "port": self.port,
                "use_ssl": self.use_ssl,
                "get_info": ALL,
                "tls": tls,
            }
            if self.connect_timeout is not None:
                server_kwargs["connect_timeout"] = self.connect_timeout
            server = Server(self.server_name, **server_kwargs)

            kwargs: dict = {"auto_referrals": False}
            if self.anonymous:
                # Анонимный bind: без учётных данных, метод ANONYMOUS.
                kwargs["authentication"] = ANONYMOUS
            else:
                kwargs["user"] = self._bind_user()
                kwargs["password"] = self.password
                kwargs["authentication"] = NTLM if self.use_ntlm else "SIMPLE"

            # StartTLS должен подняться до bind.
            if self.start_tls and not self.use_ssl:
                kwargs["auto_bind"] = AUTO_BIND_TLS_BEFORE_BIND
            else:
                kwargs["auto_bind"] = True

            self.connection = Connection(server, **kwargs)

            # База поиска: явный Base DN провайдера имеет приоритет над
            # авто-определением через defaultNamingContext.
            try:
                detected = self.connection.server.info.other[
                    "defaultNamingContext"
                ][0]
            except Exception:
                detected = ""
            self.base_dn = self.base_dn_override or detected
            if not self.base_dn:
                raise RuntimeError(
                    "Base DN не определён (нет defaultNamingContext и не задан явно)"
                )

            if self.use_ssl:
                channel = "LDAPS"
            elif self.start_tls:
                channel = "StartTLS"
            else:
                channel = "plain"
            logger.info(
                "[AD] Connected (%s, auth=%s). Base DN: %s",
                channel,
                self.bind_type.upper(),
                self.base_dn,
            )
            return True

        except Exception as e:
            logger.error(f"[AD] Connection error: {e}")
            self.connection = None
            return False

    def disconnect(self):
        if not self.connection:
            return

        try:
            if self.connection.bound:
                self.connection.unbind()
                logger.info("[AD] Connection closed")
        except Exception as e:
            logger.warning(f"[AD] unbind ignored: {e}")
        finally:
            self.connection = None

    def _ensure_connection(self) -> bool:
        try:
            if self.connection and self.connection.bound:
                return True
        except Exception:
            pass

        return self.connect()

    def _safe_search(self, *args, **kwargs) -> bool:
        try:
            return self.connection.search(*args, **kwargs)
        except LDAPSocketSendError as e:
            logger.warning(f"[AD] Socket error, reconnecting: {e}")
            self.disconnect()
            if self.connect():
                return self.connection.search(*args, **kwargs)
            return False

    def _attr_text(self, entry, attr_name: str) -> str:
        try:
            attr = entry[attr_name]
        except Exception:
            return ""

        try:
            values = attr.values
        except Exception:
            values = None

        if values:
            return "; ".join(str(v).strip() for v in values if str(v).strip())

        try:
            value = attr.value
        except Exception:
            value = None

        return str(value).strip() if value else ""

    def _split_dn_once(self, dn: str) -> tuple[str, str]:
        escaped = False

        for idx, ch in enumerate(dn or ""):
            if escaped:
                escaped = False
                continue

            if ch == "\\":
                escaped = True
                continue

            if ch == ",":
                return dn[:idx].strip(), dn[idx + 1 :].strip()

        return (dn or "").strip(), ""

    def _rdn_parts(self, rdn: str) -> tuple[str, str]:
        if "=" not in rdn:
            return "", (rdn or "").strip()

        key, value = rdn.split("=", 1)
        value = (
            value.replace("\\,", ",")
            .replace("\\=", "=")
            .replace("\\+", "+")
            .replace("\\\\", "\\")
            .strip()
        )
        return key.strip().upper(), value

    def _get_parent_container_dn(self, entry_dn: str) -> str:
        _, parent_dn = self._split_dn_once(entry_dn or "")
        return parent_dn

    def _get_container_info(self, container_dn: str) -> Dict[str, str]:
        info = {
            "name": "",
            "type": "",
            "dn": container_dn or "",
            "description": "",
        }

        if not container_dn or not self._ensure_connection():
            return info

        rdn, _ = self._split_dn_once(container_dn)
        rdn_type, rdn_value = self._rdn_parts(rdn)
        info["type"] = rdn_type
        info["name"] = rdn_value

        try:
            ok = self._safe_search(
                search_base=container_dn,
                search_filter="(objectClass=*)",
                search_scope=BASE,
                attributes=["name", "ou", "displayName", "description"],
            )
            if not ok or not self.connection.entries:
                return info

            entry = self.connection.entries[0]
            info["name"] = (
                self._attr_text(entry, "ou")
                or self._attr_text(entry, "name")
                or self._attr_text(entry, "displayName")
                or info["name"]
            )
            info["description"] = self._attr_text(entry, "description")
            return info
        except Exception as e:
            logger.warning(f"[AD] Error getting container info {container_dn}: {e}")
            return info

    def get_all_user_groups(self, username: str) -> List[str]:
        if not self._ensure_connection():
            return []

        try:
            safe_username = escape_filter_chars((username or "").strip())
            ok = self._safe_search(
                search_base=self.base_dn,
                search_filter=self._ident_filter(username),
                search_scope=SUBTREE,
                attributes=["memberOf"],
            )

            if not ok or not self.connection.entries:
                return []

            groups = set()
            queue = []

            entry = self.connection.entries[0]
            if "memberOf" in entry:
                queue.extend(entry.memberOf.values)

            while queue:
                dn = queue.pop()
                if not isinstance(dn, str):
                    continue

                rdn, _ = self._split_dn_once(dn)
                _, group_name = self._rdn_parts(rdn)
                if not group_name or group_name in groups:
                    continue

                groups.add(group_name)

                ok = self._safe_search(
                    search_base=dn,
                    search_filter="(objectClass=group)",
                    attributes=["memberOf"],
                )

                if ok and self.connection.entries:
                    group_entry = self.connection.entries[0]
                    if "memberOf" in group_entry:
                        queue.extend(group_entry.memberOf.values)

            return sorted(groups)

        except Exception as e:
            logger.error(f"[AD] Error getting groups for {username}: {e}")
            self.disconnect()
            return []

    def get_user_groups(self, username: str) -> List[str]:
        if not self._ensure_connection():
            return []

        try:
            safe_username = escape_filter_chars((username or "").strip())
            ok = self._safe_search(
                search_base=self.base_dn,
                search_filter=self._ident_filter(username),
                search_scope=SUBTREE,
                attributes=["memberOf"],
            )

            if not ok or not self.connection.entries:
                return []

            entry = self.connection.entries[0]
            groups = []

            if "memberOf" in entry:
                for dn in entry.memberOf.values:
                    if not isinstance(dn, str):
                        continue
                    rdn, _ = self._split_dn_once(dn)
                    _, group_name = self._rdn_parts(rdn)
                    if group_name:
                        groups.append(group_name)

            return groups

        except Exception as e:
            logger.error(f"[AD] Error getting groups for {username}: {e}")
            self.disconnect()
            return []

    def get_user_info(self, username: str) -> Optional[Dict]:
        if not self._ensure_connection():
            return None

        try:
            safe_username = escape_filter_chars((username or "").strip())
            ok = self._safe_search(
                search_base=self.base_dn,
                search_filter=self._ident_filter(username),
                search_scope=SUBTREE,
                attributes=[
                    "sAMAccountName",
                    "displayName",
                    "givenName",
                    "sn",
                    "mail",
                    "title",
                    "department",
                    "distinguishedName",
                ],
            )

            if not ok or not self.connection.entries:
                return None

            entry = self.connection.entries[0]
            entry_dn = getattr(entry, "entry_dn", "") or self._attr_text(
                entry, "distinguishedName"
            )
            container_dn = self._get_parent_container_dn(entry_dn)

            return {
                "login": self._attr_text(entry, "sAMAccountName"),
                "displayName": self._attr_text(entry, "displayName"),
                "givenName": self._attr_text(entry, "givenName"),
                "sn": self._attr_text(entry, "sn"),
                "mail": self._attr_text(entry, "mail"),
                "title": self._attr_text(entry, "title"),
                "department": self._attr_text(entry, "department"),
                "distinguishedName": entry_dn,
                "container": self._get_container_info(container_dn),
                "Groups": self.get_all_user_groups(username),
            }

        except Exception as e:
            logger.error(f"[AD] Error getting info for {username}: {e}")
            self.disconnect()
            return None

    def get_user_attributes(
        self, username: str, attr_names: List[str]
    ) -> Dict[str, str]:
        """Прочитать заданные атрибуты пользователя (attr -> строковое значение)."""
        names = [a for a in (attr_names or []) if a]
        if not names or not self._ensure_connection():
            return {}
        try:
            ok = self._safe_search(
                search_base=self.base_dn,
                search_filter=self._ident_filter(username),
                search_scope=SUBTREE,
                attributes=names,
            )
            if not ok or not self.connection.entries:
                return {}
            entry = self.connection.entries[0]
            return {a: self._attr_text(entry, a) for a in names}
        except Exception as e:
            logger.error(f"[AD] get_user_attributes for {username}: {e}")
            return {}

    def get_user_by_name(self, name_part: str):
        if not name_part or len(name_part.strip()) < 2:
            return []

        if not self._ensure_connection():
            return []

        name_part = name_part.strip()
        users = []

        # Каждое слово запроса должно найтись хотя бы в одном из полей, а все
        # слова обязательны (AND). Так «Иванов Иван Иванович» и «Иван Иванов»
        # находят пользователя независимо от порядка слов, а не только когда
        # весь запрос целиком является подстрокой одного поля.
        def _token_clause(token: str) -> str:
            safe = escape_filter_chars(token)
            return (
                f"(|(displayName=*{safe}*)"
                f"(givenName=*{safe}*)"
                f"(sn=*{safe}*)"
                f"(sAMAccountName=*{safe}*))"
            )

        tokens = [t for t in name_part.split() if t]
        clauses = "".join(_token_clause(t) for t in tokens) or _token_clause(name_part)

        try:
            ok = self._safe_search(
                search_base=self.base_dn,
                search_filter=(
                    f"(&(objectClass=user)(objectCategory=person){clauses})"
                ),
                search_scope=SUBTREE,
                attributes=["sAMAccountName", "displayName", "givenName", "sn"],
                size_limit=20,
            )

            if not ok:
                return []

            for entry in self.connection.entries:
                users.append(
                    {
                        "login": self._attr_text(entry, "sAMAccountName"),
                        "displayName": self._attr_text(entry, "displayName"),
                        "givenName": self._attr_text(entry, "givenName"),
                        "sn": self._attr_text(entry, "sn"),
                    }
                )

        except Exception as e:
            logger.error(f"[AD] Error searching user {name_part}: {e}")
            self.disconnect()

        return users

    # =====================================================================
    # WRITE OPERATIONS (создание/редактирование, группы, OU, пароль)
    # =====================================================================

    @staticmethod
    def _looks_like_dn(value: str) -> bool:
        v = (value or "").strip().lower()
        return ("dc=" in v) and ("=" in v) and ("," in v)

    def _result_error(self, fallback: str = "LDAP operation failed") -> str:
        res = getattr(self.connection, "result", None) or {}
        desc = str(res.get("description") or "").strip()
        msg = str(res.get("message") or "").strip()
        parts = [p for p in (desc, msg) if p]
        return " / ".join(parts) or fallback

    def _upn_suffix(self) -> str:
        suffix = str(self.upn_suffix or "").strip()
        if suffix:
            return suffix
        if self.base_dn:
            parts = [
                p.split("=", 1)[1]
                for p in self.base_dn.split(",")
                if p.strip().lower().startswith("dc=")
            ]
            if parts:
                return ".".join(parts)
        return self.domain

    def _default_user_container(self) -> Optional[str]:
        configured = str(self.default_user_ou or "").strip()
        if configured:
            return configured
        if self.base_dn:
            return f"CN=Users,{self.base_dn}"
        return None

    def get_user_dn(self, login: str) -> Optional[str]:
        if not self._ensure_connection():
            return None
        safe = escape_filter_chars((login or "").strip())
        ok = self._safe_search(
            search_base=self.base_dn,
            search_filter=self._ident_filter(login),
            search_scope=SUBTREE,
            attributes=["distinguishedName"],
            size_limit=2,
        )
        if ok and self.connection.entries:
            return self.connection.entries[0].entry_dn
        return None

    def resolve_ou_dn(self, value: str) -> Optional[str]:
        v = (value or "").strip()
        if not v:
            return None
        if self._looks_like_dn(v):
            return v
        if not self._ensure_connection():
            return None
        safe = escape_filter_chars(v)
        ok = self._safe_search(
            search_base=self.base_dn,
            search_filter=(
                f"(&(objectClass=organizationalUnit)(|(ou={safe})(name={safe})))"
            ),
            search_scope=SUBTREE,
            attributes=["distinguishedName"],
            size_limit=2,
        )
        if ok and self.connection.entries:
            return self.connection.entries[0].entry_dn
        return None

    def resolve_group_dn(self, value: str) -> Optional[str]:
        v = (value or "").strip()
        if not v:
            return None
        if self._looks_like_dn(v):
            return v
        if not self._ensure_connection():
            return None
        safe = escape_filter_chars(v)
        ok = self._safe_search(
            search_base=self.base_dn,
            search_filter=(
                f"(&(objectClass=group)"
                f"(|(sAMAccountName={safe})(cn={safe})(name={safe})))"
            ),
            search_scope=SUBTREE,
            attributes=["distinguishedName"],
            size_limit=2,
        )
        if ok and self.connection.entries:
            return self.connection.entries[0].entry_dn
        return None

    @staticmethod
    def _first_attr(attrs: dict, key: str) -> str:
        val = attrs.get(key)
        if isinstance(val, (list, tuple)):
            val = val[0] if val else ""
        return str(val).strip() if val not in (None, "") else ""

    def _paged_search(
        self,
        search_filter: str,
        attributes: List[str],
        limit: int = 5000,
        search_base: Optional[str] = None,
    ) -> List[tuple]:
        """
        Постраничный поиск — обходит серверный лимит AD (MaxPageSize=1000).
        Возвращает список (dn, attrs_dict).
        """
        if not self._ensure_connection():
            return []
        base = search_base or self.base_dn
        results: List[tuple] = []
        try:
            gen = self.connection.extend.standard.paged_search(
                search_base=base,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
                paged_size=500,
                generator=True,
            )
            for entry in gen:
                if entry.get("type") != "searchResEntry":
                    continue
                results.append(
                    (entry.get("dn", ""), entry.get("attributes", {}) or {})
                )
                if len(results) >= limit:
                    break
        except LDAPSocketSendError as e:
            logger.warning("[AD] paged_search socket error, reconnecting: %s", e)
            self.disconnect()
            if self.connect():
                return self._paged_search(
                    search_filter, attributes, limit, search_base
                )
        except Exception as e:
            logger.error("[AD] paged_search failed: %s", e)
        return results

    def list_users_in_ou(
        self, ou_dn: str, limit: int = 3000
    ) -> List[Dict[str, object]]:
        """Все пользователи в OU (включая вложенные) с данными для редактирования."""
        base = self.resolve_ou_dn(ou_dn) or (ou_dn or "").strip()
        if not base:
            return []
        rows = self._paged_search(
            "(&(objectClass=user)(objectCategory=person))",
            [
                "sAMAccountName",
                "givenName",
                "sn",
                "displayName",
                "mail",
                "employeeNumber",
                "userAccountControl",
                "pwdLastSet",
                "accountExpires",
                "whenCreated",
                "memberOf",
            ],
            limit=max(1, min(int(limit), 10000)),
            search_base=base,
        )

        users: List[Dict[str, object]] = []
        for dn, attrs in rows:
            login = self._first_attr(attrs, "sAMAccountName")
            if not login:
                continue
            try:
                uac = int(self._first_attr(attrs, "userAccountControl") or 0)
            except Exception:
                uac = 0

            # pwdLastSet == 0 (или эпоха 1601) => пароль не задан / требуется смена.
            pwd_raw = str(self._first_attr(attrs, "pwdLastSet") or "")
            password_set = bool(pwd_raw) and pwd_raw != "0" and not pwd_raw.startswith(
                "1601"
            )

            member_of = attrs.get("memberOf") or []
            if isinstance(member_of, str):
                member_of = [member_of]
            gnames: List[str] = []
            for gdn in member_of:
                rdn, _ = self._split_dn_once(str(gdn))
                _, gname = self._rdn_parts(rdn)
                if gname:
                    gnames.append(gname)

            users.append(
                {
                    "login": login,
                    "firstName": self._first_attr(attrs, "givenName"),
                    "lastName": self._first_attr(attrs, "sn"),
                    "displayName": self._first_attr(attrs, "displayName"),
                    "email": self._first_attr(attrs, "mail"),
                    "employeeNumber": self._first_attr(attrs, "employeeNumber"),
                    "enabled": not bool(uac & UAC_ACCOUNTDISABLE),
                    "passwordSet": password_set,
                    "accountExpires": account_expires_to_iso(
                        attrs.get("accountExpires")
                    ),
                    "whenCreated": format_when(attrs.get("whenCreated")),
                    "ou": self._get_parent_container_dn(dn),
                    "groups": sorted(set(gnames), key=lambda s: s.lower()),
                }
            )

        users.sort(key=lambda u: str(u["displayName"] or u["login"]).lower())
        return users

    def list_ous(self, query: str = "", limit: int = 5000) -> List[Dict[str, str]]:
        q = (query or "").strip()
        if q:
            safe = escape_filter_chars(q)
            flt = (
                f"(&(objectClass=organizationalUnit)"
                f"(|(ou=*{safe}*)(name=*{safe}*)))"
            )
        else:
            flt = "(objectClass=organizationalUnit)"

        rows = self._paged_search(
            flt,
            ["ou", "name", "description"],
            limit=max(1, min(int(limit), 10000)),
        )
        ous: List[Dict[str, str]] = []
        for dn, attrs in rows:
            name = self._first_attr(attrs, "ou") or self._first_attr(attrs, "name")
            ous.append(
                {
                    "name": name,
                    "dn": dn,
                    "description": self._first_attr(attrs, "description"),
                }
            )
        ous.sort(key=lambda o: o["dn"].lower())
        return ous

    def list_groups(self, query: str = "", limit: int = 5000) -> List[Dict[str, str]]:
        q = (query or "").strip()
        gf = self.group_filter or ""
        if q:
            safe = escape_filter_chars(q)
            flt = (
                f"(&(objectClass=group){gf}"
                f"(|(cn=*{safe}*)(sAMAccountName=*{safe}*)(name=*{safe}*)))"
            )
        elif gf:
            flt = f"(&(objectClass=group){gf})"
        else:
            flt = "(objectClass=group)"

        rows = self._paged_search(
            flt,
            ["cn", "sAMAccountName", "name", "description"],
            limit=max(1, min(int(limit), 10000)),
        )
        groups: List[Dict[str, str]] = []
        for dn, attrs in rows:
            name = (
                self._first_attr(attrs, "cn")
                or self._first_attr(attrs, "sAMAccountName")
                or self._first_attr(attrs, "name")
            )
            groups.append(
                {
                    "name": name,
                    "dn": dn,
                    "description": self._first_attr(attrs, "description"),
                }
            )
        groups.sort(key=lambda g: g["name"].lower())
        return groups

    def _get_direct_groups(self, user_dn: str) -> List[Dict[str, str]]:
        groups: List[Dict[str, str]] = []
        ok = self._safe_search(
            search_base=user_dn,
            search_filter="(objectClass=*)",
            search_scope=BASE,
            attributes=["memberOf"],
        )
        if ok and self.connection.entries:
            entry = self.connection.entries[0]
            if "memberOf" in entry:
                for dn in entry.memberOf.values:
                    if not isinstance(dn, str):
                        continue
                    rdn, _ = self._split_dn_once(dn)
                    _, name = self._rdn_parts(rdn)
                    groups.append({"name": name, "dn": dn})
        return groups

    def get_user_for_edit(self, login: str) -> Optional[Dict]:
        if not self._ensure_connection():
            return None
        safe = escape_filter_chars((login or "").strip())
        ok = self._safe_search(
            search_base=self.base_dn,
            search_filter=self._ident_filter(login),
            search_scope=SUBTREE,
            attributes=[
                "sAMAccountName",
                "givenName",
                "sn",
                "displayName",
                "mail",
                "employeeNumber",
                "userAccountControl",
                "accountExpires",
                "whenCreated",
                "distinguishedName",
                "memberOf",
            ],
        )
        if not ok or not self.connection.entries:
            return None

        entry = self.connection.entries[0]
        entry_dn = entry.entry_dn
        try:
            uac = int(entry.userAccountControl.value)
        except Exception:
            uac = 0

        try:
            account_expires = account_expires_to_iso(entry["accountExpires"].value)
        except Exception:
            account_expires = ""
        try:
            when_created = format_when(entry["whenCreated"].value)
        except Exception:
            when_created = ""

        groups: List[Dict[str, str]] = []
        if "memberOf" in entry:
            for dn in entry.memberOf.values:
                if not isinstance(dn, str):
                    continue
                rdn, _ = self._split_dn_once(dn)
                _, name = self._rdn_parts(rdn)
                groups.append({"name": name, "dn": dn})

        container_dn = self._get_parent_container_dn(entry_dn)
        return {
            "login": self._attr_text(entry, "sAMAccountName"),
            "firstName": self._attr_text(entry, "givenName"),
            "lastName": self._attr_text(entry, "sn"),
            "displayName": self._attr_text(entry, "displayName"),
            "email": self._attr_text(entry, "mail"),
            "employeeNumber": self._attr_text(entry, "employeeNumber"),
            "enabled": not bool(uac & UAC_ACCOUNTDISABLE),
            "accountExpires": account_expires,
            "whenCreated": when_created,
            "dn": entry_dn,
            "ou": container_dn,
            "container": self._get_container_info(container_dn),
            "groups": sorted(groups, key=lambda g: g["name"].lower()),
        }

    def _modify_group_member(self, group_dn: str, user_dn: str, op) -> None:
        ok = self.connection.modify(group_dn, {"member": [(op, [user_dn])]})
        if ok:
            return
        res = getattr(self.connection, "result", None) or {}
        desc = str(res.get("description") or "").lower()
        # Идемпотентность: уже состоит / уже отсутствует — не ошибка.
        if "attributeorvalueexists" in desc or "entryalreadyexists" in desc:
            return
        if "nosuchattribute" in desc or "no_such_attribute" in desc:
            return
        raise ADWriteError(
            f"Не удалось изменить членство в группе: {self._result_error()}"
        )

    def _apply_group_dns(
        self,
        user_dn: str,
        add: Optional[List[str]] = None,
        remove: Optional[List[str]] = None,
    ) -> Dict[str, list]:
        added: List[str] = []
        removed: List[str] = []
        warnings: List[str] = []
        for dn in add or []:
            try:
                self._modify_group_member(dn, user_dn, MODIFY_ADD)
                added.append(dn)
            except ADWriteError as e:
                warnings.append(str(e))
        for dn in remove or []:
            try:
                self._modify_group_member(dn, user_dn, MODIFY_DELETE)
                removed.append(dn)
            except ADWriteError as e:
                warnings.append(str(e))
        return {"added": added, "removed": removed, "warnings": warnings}

    def _apply_group_names(
        self,
        user_dn: str,
        add: Optional[List[str]] = None,
        remove: Optional[List[str]] = None,
    ) -> Dict[str, list]:
        warnings: List[str] = []
        add_dns: List[str] = []
        remove_dns: List[str] = []
        for g in add or []:
            dn = self.resolve_group_dn(g)
            if dn:
                add_dns.append(dn)
            else:
                warnings.append(f"Группа не найдена: {g}")
        for g in remove or []:
            dn = self.resolve_group_dn(g)
            if dn:
                remove_dns.append(dn)
            else:
                warnings.append(f"Группа не найдена: {g}")
        res = self._apply_group_dns(user_dn, add=add_dns, remove=remove_dns)
        res["warnings"] = warnings + res["warnings"]
        return res

    def _get_uac(self, user_dn: str) -> Optional[int]:
        ok = self._safe_search(
            search_base=user_dn,
            search_filter="(objectClass=*)",
            search_scope=BASE,
            attributes=["userAccountControl"],
        )
        if ok and self.connection.entries:
            try:
                return int(self.connection.entries[0].userAccountControl.value)
            except Exception:
                return None
        return None

    def _set_account_enabled(self, user_dn: str, enabled: bool) -> None:
        current = self._get_uac(user_dn)
        if current is None:
            current = UAC_NORMAL_ACCOUNT
        if enabled:
            new = current & ~UAC_ACCOUNTDISABLE
        else:
            new = current | UAC_ACCOUNTDISABLE
        if new == current:
            return
        ok = self.connection.modify(
            user_dn, {"userAccountControl": [(MODIFY_REPLACE, [new])]}
        )
        if not ok:
            raise ADWriteError(
                f"Не удалось изменить статус учётки: {self._result_error()}"
            )

    def _set_password(self, user_dn: str, password: str) -> None:
        try:
            ok = self.connection.extend.microsoft.modify_password(user_dn, password)
        except Exception as e:
            raise ADWriteError(f"Не удалось установить пароль: {e}")
        if not ok:
            raise ADWriteError(
                f"Не удалось установить пароль: {self._result_error()}"
            )

    def _move_to_ou(self, user_dn: str, target_ou_dn: str) -> Optional[str]:
        parent = self._get_parent_container_dn(user_dn)
        if parent.strip().lower() == target_ou_dn.strip().lower():
            return user_dn
        rdn = "+".join(safe_rdn(user_dn))
        ok = self.connection.modify_dn(
            user_dn, rdn, new_superior=target_ou_dn
        )
        if not ok:
            return None
        return f"{rdn},{target_ou_dn}"

    def create_user(
        self,
        *,
        login: str,
        first_name: str = "",
        last_name: str = "",
        display_name: str = "",
        email: str = "",
        employee_number: str = "",
        account_expires: str = "",
        password: str = "",
        ou: Optional[str] = None,
        groups: Optional[List[str]] = None,
        enabled: bool = True,
        dont_expire_password: bool = True,
    ) -> Dict:
        if not self._ensure_connection():
            raise ADWriteError("Нет соединения с Active Directory")

        login = (login or "").strip()
        if not login:
            raise ADWriteError("Не указан логин (sAMAccountName)")

        with self._write_lock:
            if self.get_user_dn(login):
                raise ADWriteError(f"Пользователь с логином {login} уже существует")

            ou_dn = self.resolve_ou_dn(ou) if ou else self._default_user_container()
            if not ou_dn:
                raise ADWriteError(f"OU не найдена: {ou!r}")

            cn = (display_name or f"{last_name} {first_name}".strip() or login).strip()
            user_dn = f"CN={escape_rdn(cn)},{ou_dn}"
            upn = f"{login}@{self._upn_suffix()}"

            uac = UAC_NORMAL_ACCOUNT | UAC_ACCOUNTDISABLE
            if dont_expire_password:
                uac |= UAC_DONT_EXPIRE_PASSWD

            attributes: Dict[str, object] = {
                "sAMAccountName": login,
                "userPrincipalName": upn,
                "displayName": cn,
                "userAccountControl": uac,
            }
            if first_name:
                attributes["givenName"] = first_name
            if last_name:
                attributes["sn"] = last_name
            if email:
                attributes["mail"] = email
            if employee_number:
                attributes["employeeNumber"] = employee_number
            ae = date_to_account_expires(account_expires)
            if ae != "0":
                attributes["accountExpires"] = ae

            ok = self.connection.add(
                user_dn,
                object_class=["top", "person", "organizationalPerson", "user"],
                attributes=attributes,
            )
            if not ok:
                raise ADWriteError(
                    f"Не удалось создать учётку: {self._result_error()}"
                )

            warnings: List[str] = []
            password_set = False
            if password:
                if not self.secure:
                    warnings.append(
                        "Пароль не задан: смена пароля требует LDAPS/StartTLS. "
                        "Учётка создана отключённой."
                    )
                else:
                    try:
                        self._set_password(user_dn, password)
                        password_set = True
                    except ADWriteError as e:
                        warnings.append(str(e))

            account_enabled = False
            if enabled and password_set:
                try:
                    self._set_account_enabled(user_dn, True)
                    account_enabled = True
                except ADWriteError as e:
                    warnings.append(str(e))
            elif enabled and not password_set:
                warnings.append(
                    "Учётка оставлена отключённой: для включения нужен пароль."
                )

            group_result = self._apply_group_names(user_dn, add=groups or [])
            warnings.extend(group_result["warnings"])

            return {
                "login": login,
                "dn": user_dn,
                "ou": ou_dn,
                "enabled": account_enabled,
                "password_set": password_set,
                "groups_added": len(group_result["added"]),
                "warnings": warnings,
            }

    def update_user(
        self,
        *,
        login: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
        employee_number: Optional[str] = None,
        account_expires: Optional[str] = None,
        ou: Optional[str] = None,
        add_groups: Optional[List[str]] = None,
        remove_groups: Optional[List[str]] = None,
        set_groups: Optional[List[str]] = None,
        new_password: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict:
        if not self._ensure_connection():
            raise ADWriteError("Нет соединения с Active Directory")

        login = (login or "").strip()
        if not login:
            raise ADWriteError("Не указан логин")

        with self._write_lock:
            user_dn = self.get_user_dn(login)
            if not user_dn:
                raise ADWriteError(f"Пользователь не найден: {login}")

            warnings: List[str] = []
            changed: List[str] = []

            mods: Dict[str, Optional[str]] = {}
            if first_name is not None:
                mods["givenName"] = first_name
            if last_name is not None:
                mods["sn"] = last_name
            if display_name is not None:
                mods["displayName"] = display_name
            if email is not None:
                mods["mail"] = email
            if employee_number is not None:
                mods["employeeNumber"] = employee_number
            if account_expires is not None:
                # '0' = никогда; иначе FILETIME. Всегда truthy => ставится явно.
                mods["accountExpires"] = date_to_account_expires(account_expires)
            if mods:
                changes = {
                    k: [(MODIFY_REPLACE, [v] if v else [])] for k, v in mods.items()
                }
                if self.connection.modify(user_dn, changes):
                    changed.append("attributes")
                else:
                    warnings.append(
                        f"Атрибуты не обновлены: {self._result_error()}"
                    )

            if set_groups is not None:
                current = {
                    g["dn"].lower(): g["dn"]
                    for g in self._get_direct_groups(user_dn)
                }
                target: Dict[str, str] = {}
                for g in set_groups:
                    dn = self.resolve_group_dn(g)
                    if dn:
                        target[dn.lower()] = dn
                    else:
                        warnings.append(f"Группа не найдена: {g}")
                to_add = [dn for k, dn in target.items() if k not in current]
                to_remove = [dn for k, dn in current.items() if k not in target]
                res = self._apply_group_dns(user_dn, add=to_add, remove=to_remove)
                warnings.extend(res["warnings"])
                if res["added"] or res["removed"]:
                    changed.append("groups")
            elif add_groups or remove_groups:
                res = self._apply_group_names(
                    user_dn, add=add_groups, remove=remove_groups
                )
                warnings.extend(res["warnings"])
                if res["added"] or res["removed"]:
                    changed.append("groups")

            if new_password:
                if not self.secure:
                    warnings.append(
                        "Пароль не изменён: требуется LDAPS/StartTLS."
                    )
                else:
                    try:
                        self._set_password(user_dn, new_password)
                        changed.append("password")
                    except ADWriteError as e:
                        warnings.append(str(e))

            if enabled is not None:
                try:
                    self._set_account_enabled(user_dn, bool(enabled))
                    changed.append("enabled" if enabled else "disabled")
                except ADWriteError as e:
                    warnings.append(str(e))

            new_dn = user_dn
            if ou:
                target_ou = self.resolve_ou_dn(ou)
                if not target_ou:
                    warnings.append(f"OU не найдена: {ou}")
                else:
                    moved = self._move_to_ou(user_dn, target_ou)
                    if moved:
                        if moved.lower() != user_dn.lower():
                            changed.append("ou")
                        new_dn = moved
                    else:
                        warnings.append(
                            f"Не удалось перенести в OU: {self._result_error()}"
                        )

            return {
                "login": login,
                "dn": new_dn,
                "changed": changed,
                "warnings": warnings,
            }


def test_ldap_connection(
    *,
    server_name: str,
    port: int,
    use_ssl: bool,
    start_tls: bool,
    validate_cert: bool,
    bind_type: str,
    bind_dn: str,
    bind_credentials: str,
    domain: str = "",
    connect_timeout: Optional[int] = None,
) -> Dict[str, object]:
    """Пробное подключение к LDAP с заданными параметрами (без сохранения).

    Возвращает {'ok', 'channel', 'base_dn', 'error'}.
    """
    bind_type = str(bind_type or "simple").strip().lower()
    anonymous = bind_type == "anonymous"
    use_ntlm = bind_type == "ntlm"
    conn: Optional[Connection] = None
    try:
        tls = None
        if use_ssl or start_tls:
            validate = ssl.CERT_REQUIRED if validate_cert else ssl.CERT_NONE
            tls = Tls(validate=validate)

        server_kwargs: dict = {
            "port": port,
            "use_ssl": use_ssl,
            "get_info": ALL,
            "tls": tls,
        }
        if connect_timeout is not None:
            server_kwargs["connect_timeout"] = connect_timeout
        server = Server(server_name, **server_kwargs)

        kwargs: dict = {"auto_referrals": False}
        if anonymous:
            kwargs["authentication"] = ANONYMOUS
        else:
            user = (bind_dn or "").strip()
            bare = not ("\\" in user or "@" in user or "=" in user)
            if user and bare and domain:
                user = f"{domain}\\{user}"
            kwargs["user"] = user
            kwargs["password"] = bind_credentials
            kwargs["authentication"] = NTLM if use_ntlm else "SIMPLE"

        if start_tls and not use_ssl:
            kwargs["auto_bind"] = AUTO_BIND_TLS_BEFORE_BIND
        else:
            kwargs["auto_bind"] = True

        conn = Connection(server, **kwargs)
        try:
            base_dn = conn.server.info.other["defaultNamingContext"][0]
        except Exception:
            base_dn = ""

        channel = "LDAPS" if use_ssl else ("StartTLS" if start_tls else "plain")
        return {"ok": True, "channel": channel, "base_dn": base_dn, "error": ""}
    except Exception as e:
        return {"ok": False, "channel": "", "base_dn": "", "error": str(e)}
    finally:
        try:
            if conn is not None and conn.bound:
                conn.unbind()
        except Exception:
            pass


_ad_connector: Optional[ADConnector] = None


def init_ad_connector() -> bool:
    global _ad_connector
    _ad_connector = ADConnector()
    return _ad_connector.connect()


def get_ad_connector() -> Optional[ADConnector]:
    return _ad_connector
