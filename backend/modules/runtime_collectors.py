"""Runtime-реестр коллекторов: редактируемый через UI список БД-коллекторов.

Источник (в порядке приоритета):
  1. настройка `collectors_json` в app_setting (задаётся из интерфейса);
  2. fallback — COLLECTORS_JSON / collectors.json / один из .env (см. collectors.py).

Пароли коллекторов хранятся в app_setting как JSON (аналогично старому
collectors.json). В UI пароль не отдаётся: только факт `password_set`, а при
сохранении пустой пароль = «оставить прежний» (сопоставление по имени/хосту).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.modules.collectors import (
    CollectorConfig,
    load_collectors,
    parse_entries,
    to_dict,
)

logger = logging.getLogger("log_analyzer")

SETTING_KEY = "collectors_json"


class RuntimeCollectors:
    def __init__(self) -> None:
        self._configs: list[CollectorConfig] = []
        self.from_db = False

    async def load(self) -> None:
        self.from_db = False
        try:
            from backend.modules.app_db import app_db

            if app_db.ready:
                raw = (await app_db.get_setting(SETTING_KEY, "")).strip()
                if raw:
                    data = json.loads(raw)
                    entries = (
                        data.get("collectors") if isinstance(data, dict) else data
                    )
                    parsed = parse_entries(entries)
                    if parsed:
                        self._configs = parsed
                        self.from_db = True
                        logger.info(
                            "Коллекторы: из БД загружено %s", len(parsed)
                        )
                        return
        except Exception:
            logger.exception("Коллекторы: ошибка чтения из БД, беру env/файл")

        # Fallback: env / файл / один из .env.
        self._configs = load_collectors()

    def all_configs(self) -> list[CollectorConfig]:
        """Все коллекторы, включая выключенные (для отображения в UI)."""
        return list(self._configs)

    def get_configs(self) -> list[CollectorConfig]:
        """Активные коллекторы для пула (fan-out поиск)."""
        return [c for c in self._configs if c.enabled]

    def public(self) -> list[dict]:
        """Список для UI — без паролей, только `password_set`."""
        return [to_dict(c, include_password=False) for c in self._configs]

    async def update(self, items: list[dict[str, Any]]) -> None:
        # Пустой пароль = «не менять»: подставляем прежний по имени/хосту.
        old_by_name = {c.name: c for c in self._configs}
        old_by_host = {c.host: c for c in self._configs}

        merged: list[dict] = []
        for it in items or []:
            entry = dict(it)
            name = str(entry.get("name") or "").strip()
            host = str(entry.get("host") or "").strip()
            pwd = entry.get("password")
            if pwd is None or str(pwd) == "":
                prev = old_by_name.get(name) or old_by_host.get(host)
                if prev is not None:
                    entry["password"] = prev.password
            merged.append(entry)

        parsed = parse_entries(merged)
        payload = json.dumps(
            {"collectors": [to_dict(c, include_password=True) for c in parsed]},
            ensure_ascii=False,
        )
        from backend.modules.app_db import app_db

        await app_db.set_setting(SETTING_KEY, payload)
        self._configs = parsed
        self.from_db = True
        logger.info("Коллекторы: сохранено %s", len(parsed))


runtime_collectors = RuntimeCollectors()
