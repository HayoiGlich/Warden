from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.schemas import (
    ADBulkCreateRequest,
    ADBulkResponse,
    ADBulkUpdateRequest,
    ADGroupListResponse,
    ADOusResponse,
    ADOuUsersResponse,
    ADUserCreate,
    ADUserDetailResponse,
    ADUserMutationResponse,
    ADUserUpdate,
    FamStatusResponse,
    FamSyncRequest,
)
from backend.modules.ad_delegation import ad_delegation
from backend.modules.authz import require_perm
from backend.modules.config import settings
from backend.services import ad_admin_service

logger = logging.getLogger("log_analyzer")

# Все операции AD выполняются под кредами вошедшего пользователя
# (делегирование); служебная учётка — только для локального админа.
ad_admin_router = APIRouter(
    prefix="/api/ad", tags=["ad-admin"], dependencies=[Depends(ad_delegation)]
)


def _ensure_ad_enabled() -> None:
    if settings.disable_ad:
        raise HTTPException(status_code=503, detail="AD отключён настройкой DISABLE_AD")


def _ensure_can_write(request: Request) -> None:
    """Изменять учётки AD может только роль с правом ad_write."""
    require_perm(request, "ad_write")


@ad_admin_router.get("/ous", response_model=ADOusResponse)
async def api_ad_ous(q: str = Query("", max_length=200)):
    success, ous = await ad_admin_service.list_ous(q)
    return {"success": success, "ous": ous}


@ad_admin_router.get("/groups-list", response_model=ADGroupListResponse)
async def api_ad_groups_list(
    q: str = Query("", max_length=200),
    limit: int = Query(2000, ge=1, le=10000),
):
    success, groups = await ad_admin_service.list_groups(q, limit)
    return {"success": success, "groups": groups}


@ad_admin_router.get("/ou-users", response_model=ADOuUsersResponse)
async def api_ad_ou_users(
    ou: str = Query(..., min_length=1, max_length=1000),
    limit: int = Query(3000, ge=1, le=10000),
):
    success, users = await ad_admin_service.list_ou_users(ou, limit)
    return {"success": success, "users": users}


@ad_admin_router.get("/user", response_model=ADUserDetailResponse)
async def api_ad_user(login: str = Query(..., min_length=1, max_length=100)):
    detail = await ad_admin_service.get_user_detail(login)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Пользователь {login} не найден")
    return {"success": True, **detail}


@ad_admin_router.get("/fam-status", response_model=FamStatusResponse)
async def api_ad_fam_status(login: str = Query(..., min_length=1, max_length=100)):
    status = await ad_admin_service.fam_status(login)
    return {"success": status.get("state") != "error", "status": status}


@ad_admin_router.post("/fam-sync", response_model=FamStatusResponse)
async def api_ad_fam_sync(request: Request, payload: FamSyncRequest):
    _ensure_can_write(request)
    status = await ad_admin_service.fam_sync(payload.login)
    return {"success": status.get("state") != "error", "status": status}


@ad_admin_router.post("/users", response_model=ADUserMutationResponse)
async def api_ad_create_user(request: Request, payload: ADUserCreate):
    _ensure_can_write(request)
    _ensure_ad_enabled()
    result = await ad_admin_service.create_user(payload.model_dump())
    return {"success": result["success"], "result": result}


@ad_admin_router.put("/users", response_model=ADUserMutationResponse)
async def api_ad_update_user(request: Request, payload: ADUserUpdate):
    _ensure_can_write(request)
    _ensure_ad_enabled()
    result = await ad_admin_service.update_user(payload.model_dump())
    return {"success": result["success"], "result": result}


@ad_admin_router.post("/users/bulk-create", response_model=ADBulkResponse)
async def api_ad_bulk_create(request: Request, payload: ADBulkCreateRequest):
    _ensure_can_write(request)
    _ensure_ad_enabled()
    if not payload.users:
        raise HTTPException(status_code=400, detail="Список пользователей пуст")
    return await ad_admin_service.bulk_create(
        [u.model_dump() for u in payload.users]
    )


@ad_admin_router.post("/users/bulk-update", response_model=ADBulkResponse)
async def api_ad_bulk_update(request: Request, payload: ADBulkUpdateRequest):
    _ensure_can_write(request)
    _ensure_ad_enabled()
    if not payload.users:
        raise HTTPException(status_code=400, detail="Список пользователей пуст")
    return await ad_admin_service.bulk_update(
        [u.model_dump() for u in payload.users]
    )
