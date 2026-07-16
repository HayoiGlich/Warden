"""Шаблоны быстрого назначения групп AD, закреплённые за пользователем.

Каждый пользователь ведёт свои шаблоны (набор групп с названием) и применяет
их при создании/редактировании учётки в AD. Данные — в БД приложения,
владелец = вошедший пользователь (чужие шаблоны не видны и не редактируются).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.modules.app_db import app_db

logger = logging.getLogger("log_analyzer")

templates_router = APIRouter(prefix="/api/ad/templates", tags=["ad-templates"])


class GroupItemIn(BaseModel):
    name: str = Field(..., max_length=300)
    dn: str = Field("", max_length=1000)


class TemplateIn(BaseModel):
    name: str = Field(..., max_length=150)
    groups: list[GroupItemIn] = []


def _owner(request: Request) -> str:
    user = request.session.get("user") or {}
    owner = str(user.get("username") or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return owner


def _groups_payload(body: TemplateIn) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for g in body.groups:
        name = g.name.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "dn": g.dn.strip()})
    return out


@templates_router.get("")
async def list_templates(request: Request):
    owner = _owner(request)
    return {"success": True, "templates": await app_db.list_templates(owner)}


@templates_router.post("")
async def create_template(request: Request, body: TemplateIn):
    owner = _owner(request)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Укажите название шаблона")
    tpl = await app_db.create_template(owner, body.name, _groups_payload(body))
    if tpl is None:
        raise HTTPException(status_code=400, detail="Не удалось создать шаблон")
    return {"success": True, "template": tpl}


@templates_router.put("/{tid}")
async def update_template(tid: int, request: Request, body: TemplateIn):
    owner = _owner(request)
    tpl = await app_db.update_template(
        tid, owner, body.name, _groups_payload(body)
    )
    if tpl is None:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    return {"success": True, "template": tpl}


@templates_router.delete("/{tid}")
async def delete_template(tid: int, request: Request):
    owner = _owner(request)
    ok = await app_db.delete_template(tid, owner)
    if not ok:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    return {"success": True}
