"""Тарифы Yandex Cloud (цены за час, ₽) для расчёта стоимости ВМ.

Хранится в app_setting под ключом `yc_tariff` (JSON). Значения по умолчанию
берутся из листа «Тариф» шаблона отчёта. Редактирует админ на вкладке «Тарифы».
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("log_analyzer")

SETTING_KEY = "yc_tariff"

# ключ -> ячейка в листе «Тариф» шаблона (строка 2 — цена за час).
FIELD_CELLS = {
    "cpu_100": "B2",   # ЦПУ обычный, 100%
    "cpu_50": "C2",    # ЦПУ обычный, 50%
    "cpu_hi": "D2",    # ЦПУ высокопроизводительный (Compute Optimized)
    "ram": "E2",       # ОЗУ обычное
    "ram_hi": "F2",    # ОЗУ высокопроизводительное
    "ssd": "G2",       # SSD
    "ssd_io": "H2",    # SSD IO
    "hdd": "I2",       # HDD
}
FIELDS = tuple(FIELD_CELLS.keys())


def _num(value) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def _clean(data) -> dict:
    data = data or {}
    return {key: _num(data.get(key)) for key in FIELDS}


class YcTariff:
    def __init__(self) -> None:
        self._values: dict | None = None

    def _template_defaults(self) -> dict:
        """Цены по умолчанию — из листа «Тариф» шаблона."""
        try:
            from openpyxl import load_workbook

            from backend.modules.yc_report import _template_path

            path = _template_path()
            if not path.is_file():
                return {key: 0.0 for key in FIELDS}
            ws = load_workbook(path, data_only=False)["Тариф"]
            return {key: _num(ws[cell].value) for key, cell in FIELD_CELLS.items()}
        except Exception:
            logger.exception("YC tariff: не удалось прочитать шаблон")
            return {key: 0.0 for key in FIELDS}

    async def load(self) -> None:
        try:
            from backend.modules.app_db import app_db

            if not app_db.ready:
                return
            raw = (await app_db.get_setting(SETTING_KEY, "")).strip()
            if raw:
                self._values = _clean(json.loads(raw))
                logger.info("YC tariff: загружено из БД")
            else:
                self._values = self._template_defaults()
                logger.info("YC tariff: используются значения из шаблона")
        except Exception:
            logger.exception("YC tariff: ошибка загрузки")

    def values(self) -> dict:
        if self._values is None:
            return self._template_defaults()
        return dict(self._values)

    async def save(self, data: dict) -> dict:
        self._values = _clean(data)
        from backend.modules.app_db import app_db

        await app_db.set_setting(
            SETTING_KEY, json.dumps(self._values, ensure_ascii=False)
        )
        logger.info("YC tariff: сохранено")
        return dict(self._values)


yc_tariff = YcTariff()
