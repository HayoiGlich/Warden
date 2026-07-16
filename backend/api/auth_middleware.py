"""Middleware авторизации: защищает все /api/*, кроме whitelist.

SPA и статику отдаём без проверки — фронтенд сам покажет форму входа
(он спрашивает /api/auth/me). Так проще, чем гейтить страницы на сервере.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Публичные API-эндпоинты (без сессии):
#   login/me   — нужны самой форме входа;
#   logout     — идемпотентен;
#   health     — для docker healthcheck.
PUBLIC_API = {
    "/api/auth/login",
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/oidc/login",
    "/api/auth/oidc/callback",
    "/api/health",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API:
            if not request.session.get("user"):
                return JSONResponse(
                    {"detail": "Требуется авторизация"}, status_code=401
                )
        return await call_next(request)
