"""Реестр ссылок на сервисы (хаб на странице «Сервисы»).

Админ собирает в одном месте ссылки на внешние сервисы (мониторинг, VMware,
вики и т.д.). Хранится в app_setting под ключом `service_links` (JSON-список).
Видят все авторизованные; редактирует только админ.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

logger = logging.getLogger("log_analyzer")

SETTING_KEY = "service_links"


def _clean(items) -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        it = it or {}
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        if not title or not url:
            continue
        out.append(
            {
                "id": str(it.get("id") or "").strip() or uuid4().hex,
                "title": title,
                "url": url,
                "description": str(it.get("description") or "").strip(),
                "icon": str(it.get("icon") or "bi-box-arrow-up-right").strip(),
                "category": str(it.get("category") or "").strip(),
            }
        )
    return out


class ServiceLinks:
    def __init__(self) -> None:
        self._links: list[dict] = []

    async def load(self) -> None:
        try:
            from backend.modules.app_db import app_db

            if not app_db.ready:
                return
            raw = (await app_db.get_setting(SETTING_KEY, "")).strip()
            if raw:
                data = json.loads(raw)
                items = data.get("services") if isinstance(data, dict) else data
                self._links = _clean(items)
            logger.info("Service links: загружено %s", len(self._links))
        except Exception:
            logger.exception("Service links: ошибка загрузки")

    async def _persist(self) -> None:
        from backend.modules.app_db import app_db

        payload = json.dumps({"services": self._links}, ensure_ascii=False)
        await app_db.set_setting(SETTING_KEY, payload)

    def public(self) -> list[dict]:
        return [dict(x) for x in self._links]

    async def save(self, items: list[dict]) -> None:
        self._links = _clean(items)
        await self._persist()
        logger.info("Service links: сохранено %s", len(self._links))


service_links = ServiceLinks()
