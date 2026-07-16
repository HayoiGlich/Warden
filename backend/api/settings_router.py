from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from backend.modules.ad_connector import (
    get_ad_connector,
    init_ad_connector,
    test_ldap_connection,
)
from backend.modules.attr_mapping import attr_mapping
from backend.modules.authz import ROLE_LABELS, ROLES, require_perm
from backend.modules.collector_pool import Collector, CollectorPool
from backend.modules.collectors import parse_entries
from backend.modules.config import settings as env_settings
from backend.modules.ldap_providers import ldap_providers
from backend.modules.role_mappings import role_mappings
from backend.modules.service_links import service_links
from backend.modules.runtime_collectors import runtime_collectors
from backend.modules.runtime_settings import (
    parse_connection_url,
    runtime_settings,
)

logger = logging.getLogger("log_analyzer")

settings_router = APIRouter(prefix="/api", tags=["settings"])


def _require_admin(request: Request) -> None:
    # Все настройки — под правом "settings" (есть только у роли admin).
    require_perm(request, "settings")


class ADSettingsIn(BaseModel):
    ad_server: Optional[str] = None
    ad_domain: Optional[str] = None
    ad_username: Optional[str] = None
    ad_password: Optional[str] = None  # пусто = не менять
    ad_use_ssl: Optional[bool] = None
    ad_start_tls: Optional[bool] = None
    ad_auth: Optional[str] = None
    ad_port: Optional[int] = None
    ad_tls_validate: Optional[bool] = None
    ad_upn_suffix: Optional[str] = None
    ad_default_user_ou: Optional[str] = None
    ad_login_group: Optional[str] = None


class LdapSettingsIn(BaseModel):
    display_name: Optional[str] = None
    vendor: Optional[str] = None
    connection_url: Optional[str] = None
    start_tls: Optional[bool] = None
    validate_cert: Optional[bool] = None
    use_pooling: Optional[bool] = None
    connect_timeout: Optional[int] = None
    bind_type: Optional[str] = None
    bind_dn: Optional[str] = None
    bind_credentials: Optional[str] = None  # пусто = не менять


class LdapTestIn(BaseModel):
    connection_url: str
    bind_type: str = "simple"
    bind_dn: str = ""
    bind_credentials: str = ""
    start_tls: bool = False
    validate_cert: bool = False
    connect_timeout: Optional[int] = None


def _reconnect_ad() -> bool:
    """Переподключить AD-коннектор с текущими эффективными настройками."""
    if env_settings.disable_ad:
        return False
    existing = get_ad_connector()
    if existing:
        try:
            existing.disconnect()
        except Exception as e:  # noqa: BLE001
            logger.warning("AD disconnect перед reconnect: %s", e)
    try:
        return bool(init_ad_connector())
    except Exception:
        logger.exception("Reconnect AD после сохранения настроек не удался")
        return False


@settings_router.get("/settings")
async def get_settings(request: Request):
    _require_admin(request)
    return {"success": True, "ad": runtime_settings.public_ad()}


@settings_router.put("/settings")
async def put_settings(request: Request, body: ADSettingsIn):
    _require_admin(request)
    # exclude_unset: сохраняем только реально присланные поля — вкладки не
    # затирают чужие настройки пустыми значениями.
    await runtime_settings.update_ad(body.model_dump(exclude_unset=True))
    ad_connected = _reconnect_ad()
    return {
        "success": True,
        "ad": runtime_settings.public_ad(),
        "ad_connected": ad_connected,
    }


@settings_router.get("/settings/ldap")
async def get_ldap_settings(request: Request):
    _require_admin(request)
    return {"success": True, "ldap": runtime_settings.public_ldap()}


@settings_router.put("/settings/ldap")
async def put_ldap_settings(request: Request, body: LdapSettingsIn):
    _require_admin(request)
    await runtime_settings.update_ldap(body.model_dump(exclude_unset=True))
    ad_connected = _reconnect_ad()
    return {
        "success": True,
        "ldap": runtime_settings.public_ldap(),
        "ad_connected": ad_connected,
    }


@settings_router.post("/settings/ldap/test")
async def test_ldap_settings(request: Request, body: LdapTestIn):
    _require_admin(request)
    parsed = parse_connection_url(body.connection_url)
    if not parsed:
        raise HTTPException(status_code=400, detail="Некорректный Connection URL")
    host, port, use_ssl = parsed

    # Пустой пароль в тесте = взять сохранённый пароль сервисной учётки.
    creds = body.bind_credentials
    if not creds and body.bind_type != "anonymous":
        creds = runtime_settings.get_ad_config().password

    result = test_ldap_connection(
        server_name=host,
        port=port,
        use_ssl=use_ssl,
        start_tls=body.start_tls,
        validate_cert=body.validate_cert,
        bind_type=body.bind_type,
        bind_dn=body.bind_dn,
        bind_credentials=creds,
        domain=runtime_settings.get_ad_config().domain,
        connect_timeout=body.connect_timeout,
    )
    return {"success": bool(result.get("ok")), **result}


# ----------------------------------------------------------------------
# LDAP-провайдеры (несколько источников, в стиле Avanpost). Активный питает
# вход, анализатор и AD-админку. Реестр живёт в app_setting.
# ----------------------------------------------------------------------
class LdapProviderIn(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = "LDAP"
    enabled: bool = True
    active: bool = False
    vendor: Optional[str] = "ad"
    host: Optional[str] = ""
    port: Optional[int] = None
    use_ssl: bool = False
    failover: List[str] = []
    start_tls: bool = False
    tls_validate: bool = False
    bind_type: Optional[str] = "simple"
    bind_dn: Optional[str] = ""
    bind_password: Optional[str] = None  # пусто = не менять
    domain: Optional[str] = ""
    connect_timeout: Optional[int] = None
    use_pooling: bool = False
    base_dn: Optional[str] = ""
    user_filter: Optional[str] = ""
    group_filter: Optional[str] = ""
    attr_login: Optional[str] = "sAMAccountName"
    attr_email: Optional[str] = "mail"
    attr_display: Optional[str] = "displayName"
    attr_first: Optional[str] = "givenName"
    attr_last: Optional[str] = "sn"
    upn_suffix: Optional[str] = ""
    default_user_ou: Optional[str] = ""
    login_group: Optional[str] = ""


class LdapProvidersIn(BaseModel):
    providers: List[LdapProviderIn]


class LdapProviderTestIn(BaseModel):
    id: Optional[str] = None
    host: str = ""
    port: Optional[int] = None
    use_ssl: bool = False
    bind_type: str = "simple"
    bind_dn: str = ""
    bind_credentials: Optional[str] = None
    start_tls: bool = False
    tls_validate: bool = False
    domain: str = ""
    connect_timeout: Optional[int] = None


@settings_router.get("/settings/ldap/providers")
async def get_ldap_providers(request: Request):
    _require_admin(request)
    return {"success": True, "providers": ldap_providers.public()}


@settings_router.put("/settings/ldap/providers")
async def put_ldap_providers(request: Request, body: LdapProvidersIn):
    _require_admin(request)
    await ldap_providers.save_all([p.model_dump() for p in body.providers])
    ad_connected = _reconnect_ad()
    return {
        "success": True,
        "providers": ldap_providers.public(),
        "ad_connected": ad_connected,
    }


@settings_router.post("/settings/ldap/providers/test")
async def test_ldap_provider(request: Request, body: LdapProviderTestIn):
    _require_admin(request)
    host = str(body.host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="Укажите хост сервера")
    use_ssl = bool(body.use_ssl)
    port = body.port or (636 if use_ssl else 389)

    # Пустой пароль — берём сохранённый у провайдера с тем же id.
    creds = body.bind_credentials
    if not creds and body.bind_type != "anonymous" and body.id:
        prov = ldap_providers.get_by_id(body.id)
        if prov:
            creds = prov.get("bind_password", "")

    result = test_ldap_connection(
        server_name=host,
        port=port,
        use_ssl=use_ssl,
        start_tls=body.start_tls,
        validate_cert=body.tls_validate,
        bind_type=body.bind_type,
        bind_dn=body.bind_dn,
        bind_credentials=creds or "",
        domain=body.domain,
        connect_timeout=body.connect_timeout,
    )
    return {"success": bool(result.get("ok")), **result}


# ----------------------------------------------------------------------
# Коллекторы (несколько БД). Реестр редактируется в UI, живёт в app_setting.
# ----------------------------------------------------------------------
class CollectorIn(BaseModel):
    name: Optional[str] = ""
    host: str
    port: Optional[int] = None
    database: Optional[str] = ""
    user: Optional[str] = ""
    password: Optional[str] = None  # пусто = не менять
    enabled: bool = True


class CollectorsIn(BaseModel):
    collectors: List[CollectorIn]


class CollectorTestIn(BaseModel):
    host: str
    port: Optional[int] = None
    database: Optional[str] = ""
    user: Optional[str] = ""
    password: Optional[str] = None  # пусто = взять сохранённый (по хосту)


def _pool(request: Request) -> Optional[CollectorPool]:
    return getattr(request.app.state, "collectors", None)


@settings_router.get("/settings/collectors")
async def get_collectors_settings(request: Request):
    _require_admin(request)
    pool = _pool(request)
    return {
        "success": True,
        "collectors": runtime_collectors.public(),
        "status": pool.status() if pool else [],
    }


@settings_router.put("/settings/collectors")
async def put_collectors_settings(request: Request, body: CollectorsIn):
    _require_admin(request)
    await runtime_collectors.update([c.model_dump() for c in body.collectors])

    pool = _pool(request)
    status: list = []
    if pool is not None:
        await pool.reload(runtime_collectors.get_configs())
        status = pool.status()

    return {
        "success": True,
        "collectors": runtime_collectors.public(),
        "status": status,
    }


@settings_router.post("/settings/collectors/test")
async def test_collector_settings(request: Request, body: CollectorTestIn):
    _require_admin(request)
    entry = body.model_dump()
    # Пустой пароль — берём сохранённый у коллектора с тем же хостом.
    if not entry.get("password"):
        host = str(entry.get("host") or "").strip()
        for c in runtime_collectors.all_configs():
            if c.host == host:
                entry["password"] = c.password
                break

    parsed = parse_entries([entry])
    if not parsed:
        raise HTTPException(
            status_code=400, detail="Некорректные параметры коллектора (нужен host)"
        )

    probe = Collector(parsed[0])
    ok = await probe.connect()
    error = probe.error
    await probe.dispose()
    return {"success": bool(ok), "connected": bool(ok), "error": error}


# ----------------------------------------------------------------------
# Доступ: соответствие «группа AD → роль» + роль по умолчанию.
# При входе через AD роль вычисляется из групп пользователя.
# ----------------------------------------------------------------------
class RoleMappingItemIn(BaseModel):
    group: str
    role: str


class RoleMappingsIn(BaseModel):
    default_role: str = "viewer"
    mappings: List[RoleMappingItemIn] = []


def _roles_catalog() -> list[dict]:
    return [{"value": r, "label": ROLE_LABELS[r]} for r in ROLES]


@settings_router.get("/settings/roles")
async def get_role_mappings(request: Request):
    _require_admin(request)
    return {
        "success": True,
        "roles": _roles_catalog(),
        **role_mappings.public(),
    }


@settings_router.put("/settings/roles")
async def put_role_mappings(request: Request, body: RoleMappingsIn):
    _require_admin(request)
    await role_mappings.save(
        body.default_role, [m.model_dump() for m in body.mappings]
    )
    return {
        "success": True,
        "roles": _roles_catalog(),
        **role_mappings.public(),
    }


class RolePreviewIn(BaseModel):
    login: str


def _ad_user_groups(login: str) -> tuple[bool, list[str]]:
    """(найден ли, список групп) — синхронно, для превью роли."""
    ad = get_ad_connector()
    if not ad:
        return False, []
    info = ad.get_user_info(login)
    if not info:
        return False, []
    return True, [str(g) for g in (info.get("Groups") or [])]


@settings_router.post("/settings/roles/preview")
async def preview_role(request: Request, body: RolePreviewIn):
    _require_admin(request)
    login = str(body.login or "").strip()
    if not login:
        raise HTTPException(status_code=400, detail="Укажите логин")
    if env_settings.disable_ad or not get_ad_connector():
        raise HTTPException(status_code=503, detail="AD недоступен")

    found, groups = await run_in_threadpool(_ad_user_groups, login)
    if not found:
        raise HTTPException(
            status_code=404, detail=f"Пользователь «{login}» в AD не найден"
        )

    detail = role_mappings.explain(groups)
    return {
        "success": True,
        "login": login,
        "groups": groups,
        "role": detail["role"],
        "role_label": ROLE_LABELS.get(detail["role"], detail["role"]),
        "used_default": detail["used_default"],
        "matched": detail["matched"],
    }


# ----------------------------------------------------------------------
# Сервисы: ссылки-хаб (мониторинг, VMware и т.д.). Смотрят все, правит админ.
# ----------------------------------------------------------------------
class ServiceLinkIn(BaseModel):
    id: Optional[str] = None
    title: str
    url: str
    description: Optional[str] = ""
    icon: Optional[str] = "bi-box-arrow-up-right"
    category: Optional[str] = ""


class ServiceLinksIn(BaseModel):
    services: List[ServiceLinkIn] = []


@settings_router.get("/services")
async def get_services(request: Request):
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return {"success": True, "services": service_links.public()}


@settings_router.put("/settings/services")
async def put_services(request: Request, body: ServiceLinksIn):
    _require_admin(request)
    await service_links.save([s.model_dump() for s in body.services])
    return {"success": True, "services": service_links.public()}


# ----------------------------------------------------------------------
# Маппинг атрибутов AD -> поля профиля (заполняется при входе).
# ----------------------------------------------------------------------
class AttrMapItemIn(BaseModel):
    attr: str
    label: Optional[str] = ""
    primary: bool = False


class AttrMapIn(BaseModel):
    mappings: List[AttrMapItemIn] = []


@settings_router.get("/settings/attributes")
async def get_attr_map(request: Request):
    _require_admin(request)
    return {"success": True, "mappings": attr_mapping.public()}


@settings_router.put("/settings/attributes")
async def put_attr_map(request: Request, body: AttrMapIn):
    _require_admin(request)
    await attr_mapping.save([m.model_dump() for m in body.mappings])
    return {"success": True, "mappings": attr_mapping.public()}
