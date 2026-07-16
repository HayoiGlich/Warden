"""Аутентификация: локальные учётки (pbkdf2) + AD-пользователи (LDAP bind).

Логика входа:
  1) если логин есть среди ЛОКАЛЬНЫХ учёток — проверяем pbkdf2-хэш (в AD не идём);
  2) иначе пробуем bind в AD с введёнными кредами;
  3) если задана группа входа (ad_login_group) — пускаем только её участников.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from backend.modules.app_db import app_db
from backend.modules.ad_connector import ADConnector, get_ad_connector
from backend.modules.attr_mapping import attr_mapping
from backend.modules.authz import ADMIN, VIEWER
from backend.modules.config import settings
from backend.modules.role_mappings import role_mappings
from backend.modules.runtime_settings import get_ad_config
from backend.modules.security import verify_password

logger = logging.getLogger("log_analyzer")


def _cn_of(group: str) -> str:
    g = str(group).strip().lower()
    if g.startswith("cn="):
        return g[3:].split(",", 1)[0]
    return g


def _in_group(groups: list, target: str) -> bool:
    t = target.strip().lower()
    for g in groups:
        gl = str(g).strip().lower()
        if t == gl or t == _cn_of(gl):
            return True
    return False


def _user_groups(username: str) -> list:
    """Группы пользователя через СЛУЖЕБНЫЙ коннектор (для OIDC, где пароля
    AD у нас нет)."""
    ad = get_ad_connector()
    if not ad:
        return []
    try:
        info = ad.get_user_info(username) or {}
        return list(info.get("Groups") or [])
    except Exception:
        logger.exception("Не удалось получить группы для %r", username)
        return []


def _ad_login(username: str, password: str) -> Optional[dict]:
    """Проверка пароля + группы + атрибуты профиля ПОД САМИМ пользователем.

    SIMPLE/LDAPS. Возвращает {"groups": [...], "profile": {...}} при успешном
    bind, иначе None (неверные креды/нет соединения). Всё читаем правами
    пользователя — служебная учётка для входа не нужна.
    """
    if not password:
        return None
    ad = ADConnector(bind_as=(username, password, "simple"))
    if not ad.connect():
        logger.info("AD bind отклонён/недоступен для %r", username)
        return None
    try:
        try:
            groups = list(ad.get_all_user_groups(username) or [])
        except Exception:
            logger.exception("Группы под %r не прочитались", username)
            groups = []
        try:
            values = ad.get_user_attributes(username, attr_mapping.attributes())
            profile = attr_mapping.build_profile(values)
        except Exception:
            logger.exception("Атрибуты профиля %r не прочитались", username)
            profile = {"display_name": "", "fields": []}
        return {"groups": groups, "profile": profile}
    finally:
        ad.disconnect()


async def authenticate(username: str, password: str) -> Optional[dict[str, Any]]:
    uname = str(username or "").strip()
    if not uname or password is None:
        return None

    # 1) Локальная учётка — в AD не проваливаемся.
    user = await app_db.get_user(uname)
    if user is not None and user.source == "local":
        if verify_password(password, user.password_hash):
            return {
                "username": user.username,
                "source": "local",
                "role": ADMIN if user.is_admin else VIEWER,
                "is_admin": bool(user.is_admin),
                "must_change_password": bool(user.must_change_password),
            }
        return None

    # 2) AD-пользователь: подключаемся ПОД НИМ (SIMPLE/LDAPS). Успешный bind
    # = пароль верный; группы читаем его же правами (служебная не нужна).
    if settings.disable_ad:
        return None
    result = await run_in_threadpool(_ad_login, uname, password)
    if result is None:
        return None  # неверные креды или нет соединения
    groups = result["groups"]
    profile = result["profile"]

    # 3a) Ограничение по группе входа (если задана) — иначе вход запрещён.
    group = str(get_ad_config().login_group or "").strip()
    if group and not _in_group(groups, group):
        logger.info("AD %r прошёл вход, но не входит в группу %r", uname, group)
        return None

    # 3b) Роль по членству в группах (мэппинг «группа → роль»).
    role = role_mappings.role_for_groups([str(g) for g in groups])
    logger.info("AD %r: роль %s (групп: %s)", uname, role, len(groups))

    return {
        "username": uname.lower(),
        "source": "ad",
        "role": role,
        "is_admin": role == ADMIN,
        "must_change_password": False,
        "display_name": profile.get("display_name") or "",
        "profile": profile.get("fields") or [],
    }


async def change_password(
    username: str, old_password: str, new_password: str
) -> tuple[bool, str]:
    user = await app_db.get_user(username)
    if user is None or user.source != "local":
        return False, "Смену пароля поддерживают только локальные учётки"
    if not verify_password(old_password, user.password_hash):
        return False, "Текущий пароль неверный"
    if len(str(new_password)) < 6:
        return False, "Новый пароль слишком короткий (минимум 6 символов)"
    await app_db.set_password(username, new_password, must_change=False)
    return True, "Пароль изменён"
