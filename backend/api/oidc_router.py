"""Вход через внешний IdP (Avanpost) по OpenID Connect — двухфакторка.

Authorization Code flow:
  1) /api/auth/oidc/login  — редирект на Avanpost (там пользователь проходит
     логин + 2FA);
  2) Avanpost возвращает на /api/auth/oidc/callback?code&state;
  3) меняем code на токены, читаем userinfo, определяем логин AD и роль,
     заводим сессию MID и редиректим на «/».

Роль OIDC-пользователя считается по его группам AD (их читает СЛУЖЕБНАЯ
учётка по логину из claim) через тот же мэппинг «группа → роль». Пароля AD у
нас нет, поэтому операции AD для OIDC-входа идут под служебной учёткой
(делегирование доступно только для входа по паролю AD).
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from backend.modules.authz import ROLE_LABELS, normalize_role, perms_for
from backend.modules.config import settings
from backend.modules.role_mappings import role_mappings
from backend.modules.runtime_settings import get_ad_config
from backend.services.auth_service import _in_group, _user_groups

logger = logging.getLogger("log_analyzer")

oidc_router = APIRouter(prefix="/api/auth/oidc", tags=["auth-oidc"])


def oidc_ready() -> bool:
    return bool(
        settings.oidc_enabled
        and settings.oidc_client_id
        and settings.oidc_auth_url
        and settings.oidc_token_url
        and settings.oidc_userinfo_url
        and settings.oidc_redirect_uri
    )


def _fail(detail: str) -> RedirectResponse:
    logger.warning("OIDC: %s", detail)
    return RedirectResponse(url=f"/?auth_error={urlencode({'m': detail})[2:]}")


@oidc_router.get("/login")
async def oidc_login(request: Request):
    if not oidc_ready():
        return _fail("Вход через Avanpost не настроен")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    request.session["oidc_state"] = state
    request.session["oidc_nonce"] = nonce

    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "response_type": "code",
        "scope": settings.oidc_scope,
        "state": state,
        "nonce": nonce,
        "prompt": "login",
    }
    return RedirectResponse(url=f"{settings.oidc_auth_url}?{urlencode(params)}")


async def _exchange_code(code: str) -> dict:
    ssl_arg = None if settings.oidc_verify_ssl else False
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            settings.oidc_token_url,
            data=payload,
            headers={"Accept": "application/json"},
            ssl=ssl_arg,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            r.raise_for_status()
            return await r.json()


async def _fetch_userinfo(access_token: str) -> dict:
    ssl_arg = None if settings.oidc_verify_ssl else False
    async with aiohttp.ClientSession() as s:
        async with s.get(
            settings.oidc_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
            ssl=ssl_arg,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            r.raise_for_status()
            return await r.json()


def _username_from_claims(claims: dict) -> str:
    for key in (settings.oidc_username_claim, "preferred_username", "sub"):
        val = str(claims.get(key) or "").strip()
        if val:
            # user@domain -> user (sAMAccountName)
            return val.split("@", 1)[0]
    return ""


@oidc_router.get("/callback")
async def oidc_callback(request: Request):
    if not oidc_ready():
        return _fail("Вход через Avanpost не настроен")

    err = request.query_params.get("error")
    if err:
        return _fail(request.query_params.get("error_description") or err)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code:
        return _fail("Не получен код авторизации")
    if not state or state != request.session.pop("oidc_state", None):
        return _fail("Неверный state (возможна CSRF-атака)")

    try:
        tokens = await _exchange_code(code)
        claims = await _fetch_userinfo(tokens["access_token"])
    except aiohttp.ClientResponseError as e:
        return _fail(f"IdP вернул ошибку {e.status}")
    except Exception as e:  # noqa: BLE001
        logger.exception("OIDC callback")
        return _fail(f"Сбой обмена с IdP: {str(e)[:120]}")

    username = _username_from_claims(claims)
    if not username:
        return _fail("В userinfo нет логина пользователя")

    # Роль — по группам AD (служебной учёткой по логину). Пусто/недоступно —
    # роль по умолчанию (Avanpost уже подтвердил личность и 2FA).
    groups = await run_in_threadpool(_user_groups, username)
    login_group = str(get_ad_config().login_group or "").strip()
    if groups and login_group and not _in_group(groups, login_group):
        return _fail(f"Пользователь не входит в группу «{login_group}»")

    role = normalize_role(role_mappings.role_for_groups([str(g) for g in groups]))
    request.session["user"] = {
        "username": username.lower(),
        "source": "oidc",
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "perms": perms_for(role),
        "is_admin": role == "admin",
        "display_name": str(claims.get("name") or username),
        "email": str(claims.get("email") or ""),
    }
    request.session["must_change_password"] = False
    request.session.pop("oidc_nonce", None)
    logger.info("OIDC-вход: %s (роль %s, групп %s)", username, role, len(groups))
    return RedirectResponse(url="/")
