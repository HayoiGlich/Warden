"""Реестр коллекторов.

«Управлялка» (веб-часть) больше не привязана к одной БД: она читает данные из
нескольких коллекторов, у каждого из которых СВОЙ PostgreSQL. Список коллекторов
задаётся (в порядке приоритета):

  1. переменная окружения COLLECTORS_JSON  — встроенный JSON-массив;
  2. файл collectors.json (путь в COLLECTORS_FILE, по умолчанию ./collectors.json);
  3. fallback — один коллектор из DB_* в .env (текущее поведение, ничего не ломает).

Формат файла::

    {
      "collectors": [
        {"name": "av-sv-event", "host": "192.168.31.225", "port": 5432,
         "database": "logs", "user": "postgres", "password": "..."}
      ]
    }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.modules.config import settings

logger = logging.getLogger("log_analyzer")


@dataclass(frozen=True)
class CollectorConfig:
    name: str
    host: str
    port: int
    database: str
    user: str
    password: str
    enabled: bool = True


def _from_dict(entry: dict, idx: int) -> Optional[CollectorConfig]:
    host = str(entry.get("host") or "").strip()
    if not host:
        logger.warning("Коллектор #%s без host пропущен: %r", idx, entry)
        return None
    name = str(entry.get("name") or host or f"collector-{idx + 1}").strip()
    db = entry.get("database")
    if db is None:
        db = entry.get("db")
    password = entry.get("password")
    return CollectorConfig(
        name=name,
        host=host,
        port=int(entry.get("port", settings.db_port)),
        database=str(db if db is not None else settings.db_name),
        user=str(entry.get("user") or settings.db_user),
        password=str(password if password is not None else settings.db_password),
        enabled=bool(entry.get("enabled", True)),
    )


def _single_from_settings() -> CollectorConfig:
    """Один коллектор из .env — обратная совместимость со старой схемой."""
    return CollectorConfig(
        name=(os.getenv("COLLECTOR_NAME", "").strip() or settings.db_host or "default"),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        enabled=True,
    )


def _load_raw() -> Optional[list]:
    inline = os.getenv("COLLECTORS_JSON", "").strip()
    if inline:
        try:
            data = json.loads(inline)
        except Exception:
            logger.exception("COLLECTORS_JSON: не удалось разобрать JSON")
        else:
            return data.get("collectors") if isinstance(data, dict) else data

    path = Path(os.getenv("COLLECTORS_FILE", "collectors.json"))
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("collectors-файл не разобран: %s", path)
        else:
            return data.get("collectors") if isinstance(data, dict) else data
    return None


def parse_entries(raw) -> list[CollectorConfig]:
    """Список словарей -> CollectorConfig (сохраняя выключенные — для UI)."""
    result: list[CollectorConfig] = []
    if isinstance(raw, list):
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            cfg = _from_dict(entry, i)
            if cfg:
                result.append(cfg)
    return result


def to_dict(cfg: CollectorConfig, *, include_password: bool = True) -> dict:
    """CollectorConfig -> словарь. Для UI пароль не отдаём — только факт."""
    data = {
        "name": cfg.name,
        "host": cfg.host,
        "port": cfg.port,
        "database": cfg.database,
        "user": cfg.user,
        "enabled": cfg.enabled,
    }
    if include_password:
        data["password"] = cfg.password
    else:
        data["password_set"] = bool(cfg.password)
    return data


def load_collectors() -> list[CollectorConfig]:
    raw = _load_raw()
    if isinstance(raw, list) and raw:
        result = [c for c in parse_entries(raw) if c.enabled]
        if result:
            logger.info(
                "Коллекторов из реестра: %s (%s)",
                len(result),
                ", ".join(c.name for c in result),
            )
            return result

    logger.info("Реестр коллекторов не задан — использую один из .env (DB_*)")
    return [_single_from_settings()]
