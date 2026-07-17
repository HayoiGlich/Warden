from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from backend.api.schemas import YcReportRequest, YcTariffIn
from backend.modules import yc_report
from backend.modules.authz import require_perm
from backend.modules.yc_tariff import yc_tariff

logger = logging.getLogger("log_analyzer")

# Отчёт по стоимости ВМ — данные инфраструктуры, доступ только у админов
# (право settings). Авторизация проверяется общим middleware для всех /api/*.
yc_router = APIRouter(prefix="/api/yc", tags=["yc-report"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@yc_router.get("/status")
async def api_yc_status(request: Request):
    require_perm(request, "settings")
    return {
        "configured": yc_report.configured(),
        "template_ready": yc_report.template_ready(),
        "zone": yc_report.settings.yc_report_zone or "",
    }


@yc_router.get("/vms")
async def api_yc_vms(request: Request, refresh: int = Query(0, ge=0, le=1)):
    require_perm(request, "settings")
    # Из кэша (быстро) отдаём даже если ключ убрали; принудительное обновление и
    # первая загрузка требуют настроенной интеграции.
    if (refresh or not yc_report.has_cache()) and not yc_report.configured():
        raise HTTPException(
            status_code=503,
            detail="Интеграция с Yandex Cloud не настроена: не найден ключ сервисного аккаунта.",
        )
    try:
        vms, ts = await run_in_threadpool(yc_report.get_vms, bool(refresh))
        tariff = await run_in_threadpool(yc_report.load_tariff)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # pragma: no cover - зависит от облака
        logger.exception("YC list_vms failed")
        raise HTTPException(status_code=502, detail=f"Ошибка Yandex Cloud: {exc}")
    return {"success": True, "vms": vms, "tariff": tariff, "cached_at": ts}


@yc_router.get("/tariff")
async def api_yc_tariff_get(request: Request):
    require_perm(request, "settings")
    return {"success": True, "tariff": yc_tariff.values()}


@yc_router.put("/tariff")
async def api_yc_tariff_put(request: Request, payload: YcTariffIn):
    require_perm(request, "settings")
    saved = await yc_tariff.save(payload.model_dump())
    # Пересчитываем стоимость в кэше по новому тарифу — без похода в облако.
    await run_in_threadpool(yc_report.recompute_cached_costs)
    return {"success": True, "tariff": saved}


@yc_router.get("/tariff/fetch")
async def api_yc_tariff_fetch(request: Request):
    """Актуальные цены за час из Yandex Cloud Billing API (для «Вставить цены»)."""
    require_perm(request, "settings")
    if not yc_report.configured():
        raise HTTPException(
            status_code=503,
            detail="Нет ключа сервисного аккаунта Yandex Cloud.",
        )
    try:
        data = await run_in_threadpool(yc_report.fetch_tariff_prices)
    except Exception as exc:  # pragma: no cover - зависит от облака/прав SA
        logger.exception("YC fetch prices failed")
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось получить цены из Yandex Cloud: {exc}",
        )
    return {"success": True, **data}


@yc_router.post("/report/xlsx")
async def api_yc_report_xlsx(request: Request, payload: YcReportRequest):
    require_perm(request, "settings")
    if not payload.rows:
        raise HTTPException(status_code=400, detail="Не выбрано ни одной машины для отчёта.")
    if not yc_report.template_ready():
        raise HTTPException(status_code=503, detail="Не найден шаблон отчёта.")
    try:
        data = await run_in_threadpool(
            yc_report.build_report_xlsx, [r.model_dump() for r in payload.rows]
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("YC build_report_xlsx failed")
        raise HTTPException(status_code=500, detail=f"Не удалось сформировать отчёт: {exc}")

    filename = f"yc_vm_report_{datetime.now():%Y%m%d}.xlsx"
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
