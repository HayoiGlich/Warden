from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.schemas import (
    ADSuggestResponse,
    CollectorStatusOut,
    CollectorsResponse,
    HealthResponse,
    SearchResponse,
    SearchStats,
    SystemResponse,
)
from backend.modules.collector_pool import CollectorPool
from backend.modules.ad_connector import init_ad_connector, get_ad_connector
from backend.modules.ad_delegation import ad_delegation
from backend.modules.config import settings
from backend.services.search_service import (
    SearchParams,
    search_events,
    ad_suggest_users,
)
from backend.services.system_service import get_system_info, get_ad_status, get_health
from backend.api.schemas import ADGroupsResponse
from backend.services.search_service import ad_get_user_groups

logger = logging.getLogger("log_analyzer")

router = APIRouter(prefix="/api", tags=["api"])


def get_collectors(request: Request) -> CollectorPool:
    pool = getattr(request.app.state, "collectors", None)
    if pool is None:
        raise RuntimeError("Collectors pool is not initialized")
    return pool


@router.get(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(ad_delegation)],
)
async def api_search(
    username: str = Query("", max_length=200),
    computer: str = Query("", max_length=200),
    period: str = Query("", pattern=r"^(|1d|7d|30d|60d)$"),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    pool: CollectorPool = Depends(get_collectors),
):
    try:
        params = SearchParams(
            username=username,
            computer=computer,
            period=period,
            limit=limit,
            offset=offset,
        )
        result = await search_events(pool, params)

        return {
            "success": True,
            "ad_connected": result.ad_connected,
            "events": result.events,
            "collectors": [
                CollectorStatusOut(**s) for s in pool.status()
            ],
            "stats": SearchStats(
                returned=len(result.events),
                successful=result.successful,
                failed=result.failed,
                limit=limit,
                offset=offset,
                total=result.total,
            ),
        }
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(e)[:300])


@router.get(
    "/ad-search",
    response_model=ADSuggestResponse,
    dependencies=[Depends(ad_delegation)],
)
async def api_ad_search(q: str = Query("", min_length=0, max_length=200)):
    success, users = await ad_suggest_users(q)
    return {"success": success, "users": users}


@router.get("/collectors", response_model=CollectorsResponse)
async def api_collectors(pool: CollectorPool = Depends(get_collectors)):
    status = await pool.probe_all()
    return {
        "success": True,
        "connected": sum(1 for s in status if s.get("connected")),
        "total": len(status),
        "collectors": [CollectorStatusOut(**s) for s in status],
    }


@router.get("/system", response_model=SystemResponse)
async def api_system():
    return {
        "system_info": get_system_info(),
        "active_directory": get_ad_status(),
    }


@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health(pool: CollectorPool = Depends(get_collectors)):
    return get_health(pool)


@router.get(
    "/ad-groups",
    response_model=ADGroupsResponse,
    dependencies=[Depends(ad_delegation)],
)
async def api_ad_groups(username: str = Query(..., min_length=1, max_length=100)):
    success, details = await ad_get_user_groups(username)

    return {
        "success": bool(success),
        "username": username,
        "displayName": details.get("displayName") or "",
        "container": details.get("container") or {},
        "groups": details.get("groups") or [],
    }


@router.post("/ad/reconnect")
async def api_ad_reconnect():
    if settings.disable_ad:
        return {"success": False, "connected": False, "detail": "AD disabled by config"}

    existing = get_ad_connector()
    if existing:
        try:
            existing.disconnect()
        except Exception as e:
            logger.warning("AD disconnect during reconnect ignored: %s", e)

    try:
        ok = init_ad_connector()
    except Exception as e:
        logger.exception("AD reconnect failed")
        return {"success": False, "connected": False, "detail": str(e)[:300]}

    return {
        "success": bool(ok),
        "connected": bool(ok),
        "detail": "AD reconnected" if ok else "AD reconnect failed",
    }
