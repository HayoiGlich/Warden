from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.modules.collector_pool import CollectorPool
from backend.modules.ad_delegation import current_ad_connector

logger = logging.getLogger("log_analyzer")

PERIOD_TO_DELTA = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "60d": timedelta(days=60),
}


def looks_like_fio(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and (" " in v)


def period_to_date_from(period: str | None) -> Optional[datetime]:
    if not period:
        return None
    delta = PERIOD_TO_DELTA.get(period)
    if not delta:
        return None
    return datetime.now(timezone.utc) - delta


@dataclass(frozen=True)
class SearchParams:
    username: str = ""
    computer: str = ""
    period: str = ""  # "", "1d", "7d", "30d"
    limit: int = 500
    offset: int = 0


@dataclass(frozen=True)
class SearchResult:
    events: list[dict]
    ad_connected: bool
    successful: int
    failed: int
    total: int


async def search_events(db: CollectorPool, params: SearchParams) -> SearchResult:
    username = (params.username or "").strip()
    computer = (params.computer or "").strip()
    limit = max(1, min(int(params.limit), 5000))
    offset = max(0, int(params.offset))
    date_from = period_to_date_from(params.period)

    ad = current_ad_connector()
    ad_connected = bool(ad and ad.connection and ad.connection.bound)

    # 1) If looks like FIO -> resolve logins from AD
    search_logins: list[str] = []
    if username and looks_like_fio(username) and ad_connected:
        try:
            ad_users = ad.get_user_by_name(username)
            search_logins = [u.get("login", "") for u in ad_users if u.get("login")]
        except Exception:
            logger.exception("AD lookup failed for fio=%r", username)
            search_logins = []

    # 2) Fetch from DB + total
    if search_logins:
        total = await db.count_event(
            usernames=search_logins,
            computer=computer,
            date_from=date_from,
        )
        events = await db.fetch_event(
            username=None,
            usernames=search_logins,
            computer=computer,
            date_from=date_from,
            limit=limit,
            offset=offset,
        )
    else:
        # Если это ФИО, но логинов нет — разумно вернуть пусто
        if username and looks_like_fio(username) and ad_connected:
            total = 0
            events = []
        else:
            total = await db.count_event(
                username=username,
                computer=computer,
                date_from=date_from,
            )
            events = await db.fetch_event(
                username=username,
                computer=computer,
                date_from=date_from,
                limit=limit,
                offset=offset,
            )

    successful = sum(1 for e in events if e.get("event_id") == 4624)
    failed = sum(1 for e in events if e.get("event_id") == 4625)

    return SearchResult(
        events=events,
        ad_connected=ad_connected,
        successful=successful,
        failed=failed,
        total=total,
    )


async def ad_suggest_users(q: str) -> tuple[bool, list[dict]]:
    ad = current_ad_connector()
    if not ad or not ad.connection or not ad.connection.bound:
        return False, []

    query = (q or "").strip()
    if not query:
        return True, []

    try:
        users = ad.get_user_by_name(query)
        return True, users[:20]
    except Exception:
        logger.exception("AD suggest failed for q=%r", query)
        return False, []


async def ad_get_user_groups(username: str) -> tuple[bool, dict]:
    ad = current_ad_connector()
    if not ad or not ad.connection or not ad.connection.bound:
        return False, {
            "displayName": "",
            "container": {
                "name": "",
                "type": "",
                "dn": "",
                "description": "",
            },
            "groups": [],
        }

    try:
        info = ad.get_user_info(username) or {}
        container = info.get("container") or {}

        return True, {
            "displayName": str(info.get("displayName") or ""),
            "container": {
                "name": str(container.get("name") or ""),
                "type": str(container.get("type") or ""),
                "dn": str(container.get("dn") or ""),
                "description": str(container.get("description") or ""),
            },
            "groups": list(info.get("Groups") or []),
        }
    except Exception:
        logger.exception("AD groups failed for %r", username)
        return False, {
            "displayName": "",
            "container": {
                "name": "",
                "type": "",
                "dn": "",
                "description": "",
            },
            "groups": [],
        }
