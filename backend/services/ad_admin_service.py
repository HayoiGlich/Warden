from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from backend.modules.ad_connector import ADWriteError
from backend.modules.ad_delegation import current_ad_connector
from backend.modules.fam_client import get_fam_client

logger = logging.getLogger("log_analyzer")


def _connector():
    # Делегирование: коннектор вошедшего пользователя (или служебный для
    # локального админа). Операции идут под его правами в AD.
    ad = current_ad_connector()
    if not ad or not ad.connection or not ad.connection.bound:
        raise ADWriteError("Active Directory недоступен. Проверьте подключение.")
    return ad


def _result(
    login: str,
    *,
    success: bool,
    action: str,
    detail: str = "",
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "login": login,
        "success": bool(success),
        "action": action,
        "detail": detail,
        "warnings": warnings or [],
    }


# ---------------------------------------------------------------------------
# Справочники (OU и группы) для выпадающих списков на фронтенде
# ---------------------------------------------------------------------------


async def list_ous(query: str = "") -> tuple[bool, list[dict]]:
    try:
        ad = _connector()
    except ADWriteError:
        return False, []
    try:
        ous = await run_in_threadpool(ad.list_ous, query)
        return True, ous
    except Exception:
        logger.exception("AD list_ous failed")
        return False, []


async def list_groups(query: str = "", limit: int = 200) -> tuple[bool, list[dict]]:
    try:
        ad = _connector()
    except ADWriteError:
        return False, []
    try:
        groups = await run_in_threadpool(ad.list_groups, query, limit)
        return True, groups
    except Exception:
        logger.exception("AD list_groups failed")
        return False, []


async def list_ou_users(ou: str, limit: int = 3000) -> tuple[bool, list[dict]]:
    try:
        ad = _connector()
    except ADWriteError:
        return False, []
    try:
        users = await run_in_threadpool(ad.list_users_in_ou, ou, limit)
        return True, users
    except Exception:
        logger.exception("AD list_users_in_ou failed")
        return False, []


async def get_user_detail(login: str) -> Optional[dict]:
    try:
        ad = _connector()
    except ADWriteError:
        return None
    try:
        return await run_in_threadpool(ad.get_user_for_edit, login)
    except Exception:
        logger.exception("AD get_user_for_edit failed for %r", login)
        return None


# ---------------------------------------------------------------------------
# Avanpost FAM — проверка попадания учётки и синхронизация
# ---------------------------------------------------------------------------


async def fam_status(login: str) -> dict[str, Any]:
    """Статус учётки в Avanpost FAM (найдена / отсутствует / причина)."""
    login = str(login or "").strip()
    return await get_fam_client().lookup_user(login)


async def fam_sync(login: str) -> dict[str, Any]:
    """Принудительная синхронизация: сбрасываем кэш и заново спрашиваем FAM."""
    login = str(login or "").strip()
    client = get_fam_client()
    client.invalidate_user(login)
    return await client.lookup_user(login)


# ---------------------------------------------------------------------------
# Создание / редактирование (одиночное)
# ---------------------------------------------------------------------------


async def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    login = str(payload.get("login") or "").strip()
    try:
        ad = _connector()
    except ADWriteError as e:
        return _result(login, success=False, action="error", detail=str(e))

    try:
        info = await run_in_threadpool(
            lambda: ad.create_user(
                login=login,
                first_name=str(payload.get("firstName") or ""),
                last_name=str(payload.get("lastName") or ""),
                display_name=str(payload.get("displayName") or ""),
                email=str(payload.get("email") or ""),
                employee_number=str(payload.get("employeeNumber") or ""),
                account_expires=str(payload.get("accountExpires") or ""),
                password=str(payload.get("password") or ""),
                ou=str(payload.get("ou") or "") or None,
                groups=list(payload.get("groups") or []),
                enabled=bool(payload.get("enabled", True)),
            )
        )
    except ADWriteError as e:
        return _result(login, success=False, action="error", detail=str(e))
    except Exception as e:
        logger.exception("AD create_user failed for %r", login)
        return _result(login, success=False, action="error", detail=str(e)[:300])

    detail = "Учётка создана"
    if not info.get("enabled"):
        detail += " (отключена)"
    if info.get("groups_added"):
        detail += f", групп добавлено: {info['groups_added']}"
    return _result(
        login,
        success=True,
        action="created",
        detail=detail,
        warnings=info.get("warnings"),
    )


async def update_user(payload: dict[str, Any]) -> dict[str, Any]:
    login = str(payload.get("login") or "").strip()
    try:
        ad = _connector()
    except ADWriteError as e:
        return _result(login, success=False, action="error", detail=str(e))

    set_groups = payload.get("setGroups")
    try:
        info = await run_in_threadpool(
            lambda: ad.update_user(
                login=login,
                first_name=payload.get("firstName"),
                last_name=payload.get("lastName"),
                display_name=payload.get("displayName"),
                email=payload.get("email"),
                employee_number=payload.get("employeeNumber"),
                account_expires=payload.get("accountExpires"),
                ou=str(payload.get("ou") or "") or None,
                add_groups=list(payload.get("addGroups") or []),
                remove_groups=list(payload.get("removeGroups") or []),
                set_groups=list(set_groups) if set_groups is not None else None,
                new_password=str(payload.get("newPassword") or "") or None,
                enabled=payload.get("enabled"),
            )
        )
    except ADWriteError as e:
        return _result(login, success=False, action="error", detail=str(e))
    except Exception as e:
        logger.exception("AD update_user failed for %r", login)
        return _result(login, success=False, action="error", detail=str(e)[:300])

    changed = info.get("changed") or []
    detail = (
        f"Изменено: {', '.join(changed)}" if changed else "Изменений не потребовалось"
    )
    return _result(
        login,
        success=True,
        action="updated",
        detail=detail,
        warnings=info.get("warnings"),
    )


# ---------------------------------------------------------------------------
# Массовые операции — последовательно, с построчным отчётом
# ---------------------------------------------------------------------------


def _summary(results: list[dict]) -> dict[str, Any]:
    succeeded = sum(1 for r in results if r.get("success"))
    return {
        "success": all(r.get("success") for r in results) if results else True,
        "processed": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


async def bulk_create(users: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for payload in users:
        results.append(await create_user(payload))
    return _summary(results)


async def bulk_update(users: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for payload in users:
        results.append(await update_user(payload))
    return _summary(results)
