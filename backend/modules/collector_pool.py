"""Пул коллекторов: параллельный (fan-out) доступ к нескольким БД.

Каждый коллектор — отдельный PostgreSQL со своей копией таблицы logins. Поиск
выполняется по всем подключённым коллекторам одновременно, результаты
помечаются именем коллектора, сливаются, сортируются по времени и пагинируются
на стороне «управлялки».
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.modules.collectors import CollectorConfig, load_collectors
from backend.modules.database import Database

logger = logging.getLogger("log_analyzer")

_MIN_TS = datetime.min.replace(tzinfo=timezone.utc)


def _parse_ts(value) -> datetime:
    """ISO-строка времени -> aware datetime для корректной сортировки."""
    if not value:
        return _MIN_TS
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return _MIN_TS
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Collector:
    def __init__(self, cfg: CollectorConfig) -> None:
        self.cfg = cfg
        self.db = Database()
        self.connected = False
        self.error = ""

    async def connect(self) -> bool:
        try:
            await self.db.connect(
                user=self.cfg.user,
                password=self.cfg.password,
                host=self.cfg.host,
                database=self.cfg.database,
                port=self.cfg.port,
                create_tables=False,
            )
            self.connected = True
            self.error = ""
        except Exception as e:  # noqa: BLE001 — недоступный коллектор не должен ронять старт
            self.connected = False
            self.error = str(e)[:300]
            logger.warning(
                "Коллектор %r (%s) недоступен: %s",
                self.cfg.name,
                self.cfg.host,
                self.error,
            )
        return self.connected

    async def probe(self) -> bool:
        """Живая проверка доступности: SELECT 1 (или повторный connect)."""
        if self.db.engine is None:
            return await self.connect()
        try:
            async with self.db.engine.connect() as conn:
                await conn.exec_driver_sql("SELECT 1")
            self.connected = True
            self.error = ""
        except Exception as e:  # noqa: BLE001
            self.connected = False
            self.error = str(e)[:300]
            logger.warning(
                "Коллектор %r (%s) недоступен при проверке: %s",
                self.cfg.name,
                self.cfg.host,
                self.error,
            )
        return self.connected

    async def dispose(self) -> None:
        try:
            if self.db.engine:
                await self.db.engine.dispose()
        except Exception:
            logger.exception("dispose коллектора %r", self.cfg.name)


class CollectorPool:
    def __init__(self, configs: Optional[list[CollectorConfig]] = None) -> None:
        self.collectors: list[Collector] = [
            Collector(c) for c in (configs if configs is not None else load_collectors())
        ]

    async def connect_all(self) -> None:
        await asyncio.gather(*(c.connect() for c in self.collectors))
        ok = sum(1 for c in self.collectors if c.connected)
        logger.info("[INIT] Коллекторы: подключено %s из %s", ok, len(self.collectors))

    def set_configs(self, configs: list[CollectorConfig]) -> None:
        self.collectors = [Collector(c) for c in configs]

    async def reload(self, configs: list[CollectorConfig]) -> None:
        """Пересобрать пул из нового списка: закрыть старые, подключить новые."""
        await self.dispose_all()
        self.set_configs(configs)
        await self.connect_all()

    async def dispose_all(self) -> None:
        await asyncio.gather(*(c.dispose() for c in self.collectors))

    async def probe_all(self) -> list[dict]:
        """Перепроверить доступность всех коллекторов и вернуть статус."""
        await asyncio.gather(*(c.probe() for c in self.collectors))
        return self.status()

    @property
    def any_connected(self) -> bool:
        return any(c.connected for c in self.collectors)

    def status(self) -> list[dict]:
        return [
            {
                "name": c.cfg.name,
                "host": c.cfg.host,
                "connected": c.connected,
                "error": c.error,
            }
            for c in self.collectors
        ]

    def _active(self) -> list[Collector]:
        return [c for c in self.collectors if c.connected]

    async def count_event(self, **kwargs) -> int:
        active = self._active()
        if not active:
            return 0
        results = await asyncio.gather(
            *(c.db.count_event(**kwargs) for c in active),
            return_exceptions=True,
        )
        total = 0
        for c, res in zip(active, results):
            if isinstance(res, Exception):
                logger.warning("count_event на %r: %s", c.cfg.name, res)
                continue
            total += int(res or 0)
        return total

    async def fetch_event(self, *, limit: int, offset: int, **kwargs) -> list[dict]:
        active = self._active()
        if not active:
            return []

        # Чтобы корректно отдать срез [offset:offset+limit] после слияния всех
        # коллекторов, с каждого берём первые (offset+limit) строк по времени.
        per = max(1, min(limit + offset, 5000))
        results = await asyncio.gather(
            *(c.db.fetch_event(limit=per, offset=0, **kwargs) for c in active),
            return_exceptions=True,
        )

        merged: list[dict] = []
        for c, rows in zip(active, results):
            if isinstance(rows, Exception):
                logger.warning("fetch_event на %r: %s", c.cfg.name, rows)
                continue
            for row in rows:
                row["collector"] = c.cfg.name
                merged.append(row)

        merged.sort(key=lambda r: _parse_ts(r.get("time_created")), reverse=True)
        return merged[offset : offset + limit]
