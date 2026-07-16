"""Делегирование: операции AD под кредами вошедшего пользователя.

Идея: вместо служебной учётки (eventsreader) операции AD (поиск, чтение
групп, создание/правка) выполняются от имени вошедшего пользователя — его
доменные права и решают, что можно. Служебная учётка остаётся только для
фоновых/до-входных задач (статус AD, вычисление роли, анализатор).

Механика: на время запроса в contextvar кладётся ADConnector, подключённый
под пользователем. Сервисы вызывают `current_ad_connector()` — он вернёт
пользовательский коннектор, если он есть, иначе служебный синглтон.
Локальный админ (без доменных кредов) всегда работает через служебный.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Optional

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from starlette.requests import Request

from backend.modules.ad_connector import ADConnector, get_ad_connector
from backend.modules import user_creds

logger = logging.getLogger("log_analyzer")

# Пользовательский коннектор текущего запроса (None => служебный).
_req_connector: contextvars.ContextVar[Optional[ADConnector]] = (
    contextvars.ContextVar("ad_req_connector", default=None)
)


def current_ad_connector() -> Optional[ADConnector]:
    """Коннектор для операций AD: пользовательский (делегирование) или служебный."""
    delegated = _req_connector.get()
    if delegated is not None:
        return delegated
    return get_ad_connector()


def _build_user_connector(username: str, password: str) -> Optional[ADConnector]:
    # SIMPLE-bind поверх LDAPS (не NTLM — тот требует MD4, убранный в OpenSSL 3).
    # ADConnector сам подставит NetBIOS-домен к голому логину.
    ad = ADConnector(bind_as=(username, password, "simple"))
    return ad if ad.connect() else None


async def ad_delegation(request: Request):
    """FastAPI-зависимость: на время запроса подключиться под пользователем.

    AD-пользователь без действующих кредов (рестарт сервиса / истёк токен) —
    401, чтобы фронт предложил войти заново. Локальный админ идёт мимо
    делегирования (служебная учётка).
    """
    user = request.session.get("user") or {}
    if user.get("source") != "ad":
        yield  # локальная учётка — служебный коннектор
        return

    creds = user_creds.get(request.session.get("cred_token"))
    if not creds:
        raise HTTPException(
            status_code=401,
            detail="Сессия для операций с AD истекла — войдите заново",
        )

    connector = await run_in_threadpool(
        _build_user_connector, creds["username"], creds["password"]
    )
    if connector is None:
        raise HTTPException(
            status_code=401,
            detail="Не удалось подключиться к AD под вашей учётной записью",
        )

    token = _req_connector.set(connector)
    try:
        yield
    finally:
        _req_connector.reset(token)
        await run_in_threadpool(connector.disconnect)
