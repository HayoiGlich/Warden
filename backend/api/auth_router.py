from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.modules.authz import ROLE_LABELS, normalize_role, perms_for
from backend.modules.config import settings
from backend.modules import user_creds
from backend.api.oidc_router import oidc_ready
from backend.services.auth_service import authenticate, change_password

logger = logging.getLogger("log_analyzer")

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(..., max_length=150)
    password: str = Field(..., max_length=300)


class ChangePwIn(BaseModel):
    old_password: str = Field(..., max_length=300)
    new_password: str = Field(..., max_length=300)


def _session_user(principal: dict) -> dict:
    role = normalize_role(principal.get("role"))
    return {
        "username": principal["username"],
        "source": principal["source"],
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "perms": perms_for(role),
        "is_admin": principal["is_admin"],
        "display_name": principal.get("display_name") or "",
        "profile": principal.get("profile") or [],
    }


@auth_router.post("/login")
async def login(request: Request, body: LoginIn):
    principal = await authenticate(body.username, body.password)
    if not principal:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    request.session["user"] = _session_user(principal)
    request.session["must_change_password"] = principal["must_change_password"]

    # Делегирование: для AD-пользователя запоминаем его пароль на время
    # сессии (в памяти процесса; в cookie — только токен), чтобы операции AD
    # шли под его правами. Локальному админу креды AD не нужны.
    old_token = request.session.pop("cred_token", None)
    user_creds.drop(old_token)
    if principal["source"] == "ad":
        request.session["cred_token"] = user_creds.put(
            principal["username"], body.password
        )

    logger.info(
        "Вход: %s (%s)", principal["username"], principal["source"]
    )
    return {
        "success": True,
        "user": request.session["user"],
        "must_change_password": principal["must_change_password"],
    }


@auth_router.post("/logout")
async def logout(request: Request):
    user_creds.drop(request.session.get("cred_token"))
    request.session.clear()
    return {"success": True}


@auth_router.get("/me")
async def me(request: Request):
    user = request.session.get("user")
    return {
        "authenticated": bool(user),
        "user": user,
        "must_change_password": bool(request.session.get("must_change_password")),
        "oidc_enabled": oidc_ready(),
        "oidc_label": settings.oidc_button_label,
    }


@auth_router.post("/change-password")
async def change_pw(request: Request, body: ChangePwIn):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    ok, msg = await change_password(
        user["username"], body.old_password, body.new_password
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    request.session["must_change_password"] = False
    return {"success": True, "detail": msg}
