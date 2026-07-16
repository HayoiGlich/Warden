"""Маппинг «атрибут AD → поле профиля».

Админ задаёт, какие атрибуты каталога подтягивать в профиль пользователя
после входа и как их подписывать. При входе (по паролю AD — правами самого
пользователя) эти атрибуты читаются и кладутся в сессию как профиль, который
показывается в интерфейсе.

Хранится в app_setting под ключом `profile_attr_map` (JSON).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("log_analyzer")

SETTING_KEY = "profile_attr_map"

# Разумный дефолт для Active Directory.
_DEFAULT = [
    {"attr": "displayName", "label": "ФИО", "primary": True},
    {"attr": "mail", "label": "Email", "primary": False},
    {"attr": "title", "label": "Должность", "primary": False},
    {"attr": "department", "label": "Отдел", "primary": False},
    {"attr": "telephoneNumber", "label": "Телефон", "primary": False},
]


def _clean(items) -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        it = it or {}
        attr = str(it.get("attr") or "").strip()
        if not attr:
            continue
        out.append(
            {
                "attr": attr,
                "label": str(it.get("label") or attr).strip() or attr,
                "primary": bool(it.get("primary")),
            }
        )
    # Ровно один primary (первый помеченный, иначе первый в списке).
    if out:
        primary_idx = next(
            (i for i, m in enumerate(out) if m["primary"]), 0
        )
        for i, m in enumerate(out):
            m["primary"] = i == primary_idx
    return out


class AttrMapping:
    def __init__(self) -> None:
        self._map: list[dict] = list(_DEFAULT)

    async def load(self) -> None:
        try:
            from backend.modules.app_db import app_db

            if not app_db.ready:
                return
            raw = (await app_db.get_setting(SETTING_KEY, "")).strip()
            if not raw:
                self._map = _clean(_DEFAULT)
                await self._persist()
                logger.info("Attr mapping: инициализирован дефолтом")
                return
            data = json.loads(raw)
            items = data.get("mappings") if isinstance(data, dict) else data
            self._map = _clean(items) or _clean(_DEFAULT)
            logger.info("Attr mapping: загружено %s", len(self._map))
        except Exception:
            logger.exception("Attr mapping: ошибка загрузки")

    async def _persist(self) -> None:
        from backend.modules.app_db import app_db

        payload = json.dumps({"mappings": self._map}, ensure_ascii=False)
        await app_db.set_setting(SETTING_KEY, payload)

    def attributes(self) -> list[str]:
        """Список AD-атрибутов для чтения при входе."""
        return [m["attr"] for m in self._map]

    def build_profile(self, values: dict) -> dict:
        """AD-значения (attr→value) -> {display_name, fields:[{label,value}]}."""
        fields: list[dict] = []
        display_name = ""
        for m in self._map:
            val = str(values.get(m["attr"]) or "").strip()
            if m["primary"] and val:
                display_name = val
            if val:
                fields.append({"label": m["label"], "value": val})
        return {"display_name": display_name, "fields": fields}

    def public(self) -> list[dict]:
        return [dict(m) for m in self._map]

    async def save(self, items: list[dict]) -> None:
        self._map = _clean(items) or _clean(_DEFAULT)
        await self._persist()
        logger.info("Attr mapping: сохранено %s", len(self._map))


attr_mapping = AttrMapping()
