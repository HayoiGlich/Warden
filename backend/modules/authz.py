"""Роли и права доступа (RBAC).

Три роли, от старшей к младшей: admin > operator > viewer.
Роль вычисляется при входе (для AD — из групп, см. role_mappings) и кладётся
в сессию вместе с плоским списком прав (perms). Проверки на бэкенде идут по
конкретному праву, а не по роли, — так проще расширять.

Права:
  ad_read   — просмотр пользователей AD и журналов (есть у всех авторизованных);
  ad_write  — создание/редактирование учёток AD;
  settings  — доступ к настройкам (провайдеры, коллекторы, доступ).
"""

from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException
from starlette.requests import Request

ADMIN = "admin"
OPERATOR = "operator"
VIEWER = "viewer"

ROLES = (ADMIN, OPERATOR, VIEWER)

# Чем больше ранг — тем больше прав (для выбора старшей из нескольких).
ROLE_RANK = {VIEWER: 1, OPERATOR: 2, ADMIN: 3}

ROLE_LABELS = {
    ADMIN: "Администратор",
    OPERATOR: "Оператор",
    VIEWER: "Просмотр",
}

ROLE_PERMS: dict[str, tuple[str, ...]] = {
    ADMIN: ("ad_read", "ad_write", "logs", "settings"),
    OPERATOR: ("ad_read", "ad_write", "logs"),
    VIEWER: ("ad_read", "logs"),
}


def normalize_role(role: str) -> str:
    r = str(role or "").strip().lower()
    return r if r in ROLE_RANK else VIEWER


def perms_for(role: str) -> list[str]:
    return list(ROLE_PERMS.get(normalize_role(role), ROLE_PERMS[VIEWER]))


def highest_role(roles: Iterable[str]) -> str | None:
    """Старшая роль из набора (по рангу) или None, если набор пуст."""
    ranked = [normalize_role(r) for r in roles if r]
    if not ranked:
        return None
    return max(ranked, key=lambda r: ROLE_RANK[r])


def session_perms(request: Request) -> set[str]:
    """Права из сессии. Совместимость со старыми сессиями: is_admin ⇒ admin."""
    user = request.session.get("user") or {}
    perms = user.get("perms")
    if isinstance(perms, list):
        return set(perms)
    # Старая сессия без perms — выводим из is_admin.
    return set(ROLE_PERMS[ADMIN] if user.get("is_admin") else ROLE_PERMS[VIEWER])


def require_perm(request: Request, perm: str) -> None:
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    if perm not in session_perms(request):
        raise HTTPException(
            status_code=403, detail="Недостаточно прав для этого действия"
        )
