"""Соответствие «группа AD → роль» + роль по умолчанию.

Настраивается в UI (вкладка «Доступ»). При входе через AD берём группы
пользователя, сопоставляем с этим списком и выбираем СТАРШУЮ подходящую роль;
если ни одна группа не совпала — роль по умолчанию (обычно viewer).

Хранится в app_setting под ключом `role_mappings` (JSON):
  {"default_role": "viewer",
   "mappings": [{"group": "MID-Admins", "role": "admin"}, ...]}
"""

from __future__ import annotations

import json
import logging

from backend.modules.authz import ROLE_RANK, VIEWER, highest_role, normalize_role

logger = logging.getLogger("log_analyzer")

SETTING_KEY = "role_mappings"


def _cn_of(group: str) -> str:
    g = str(group).strip().lower()
    if g.startswith("cn="):
        return g[3:].split(",", 1)[0]
    return g


def _group_matches(user_groups: list[str], target: str) -> bool:
    t = str(target).strip().lower()
    if not t:
        return False
    tc = _cn_of(t)
    for g in user_groups:
        gl = str(g).strip().lower()
        if t == gl or tc == _cn_of(gl):
            return True
    return False


class RoleMappings:
    def __init__(self) -> None:
        self._default_role: str = VIEWER
        self._mappings: list[dict] = []

    async def load(self) -> None:
        try:
            from backend.modules.app_db import app_db

            if not app_db.ready:
                return
            raw = (await app_db.get_setting(SETTING_KEY, "")).strip()
            if not raw:
                # Первичное состояние: без правил, все AD-входы — viewer.
                self._default_role = VIEWER
                self._mappings = []
                await self._persist()
                logger.info("Role mappings: инициализированы пустыми (default=viewer)")
                return
            data = json.loads(raw)
            self._default_role = normalize_role(data.get("default_role"))
            self._mappings = self._clean(data.get("mappings"))
            logger.info("Role mappings: загружено правил %s", len(self._mappings))
        except Exception:
            logger.exception("Role mappings: ошибка загрузки")

    @staticmethod
    def _clean(items) -> list[dict]:
        out: list[dict] = []
        for it in items or []:
            group = str((it or {}).get("group") or "").strip()
            if not group:
                continue
            out.append({"group": group, "role": normalize_role(it.get("role"))})
        return out

    async def _persist(self) -> None:
        from backend.modules.app_db import app_db

        payload = json.dumps(
            {"default_role": self._default_role, "mappings": self._mappings},
            ensure_ascii=False,
        )
        await app_db.set_setting(SETTING_KEY, payload)

    def role_for_groups(self, user_groups: list[str]) -> str:
        """Старшая роль среди совпавших правил; иначе — роль по умолчанию."""
        matched = [
            m["role"] for m in self._mappings if _group_matches(user_groups, m["group"])
        ]
        chosen = highest_role(matched)
        return chosen or self._default_role

    def explain(self, user_groups: list[str]) -> dict:
        """Подробности вычисления роли (для превью в UI)."""
        matched = [
            dict(m) for m in self._mappings if _group_matches(user_groups, m["group"])
        ]
        chosen = highest_role([m["role"] for m in matched])
        return {
            "role": chosen or self._default_role,
            "used_default": chosen is None,
            "default_role": self._default_role,
            "matched": matched,
        }

    def public(self) -> dict:
        return {
            "default_role": self._default_role,
            "mappings": [dict(m) for m in self._mappings],
        }

    async def save(self, default_role: str, mappings: list[dict]) -> None:
        self._default_role = normalize_role(default_role)
        cleaned = self._clean(mappings)
        # Стабильный порядок: сначала более старшие роли (админ сверху).
        cleaned.sort(key=lambda m: ROLE_RANK[m["role"]], reverse=True)
        self._mappings = cleaned
        await self._persist()
        logger.info("Role mappings: сохранено правил %s", len(cleaned))


role_mappings = RoleMappings()
