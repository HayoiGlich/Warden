"""База приложения (Postgres) — учётки авторизации и настройки.

Отдельная от баз коллекторов (те read-only, свои). Здесь read-write:
  - app_user   — локальные учётки (логин + pbkdf2-хэш);
  - app_setting — редактируемые настройки (ключ/значение).

AD-пользователи здесь НЕ хранятся: они проверяются «вживую» через LDAP-bind.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from backend.modules.config import settings
from backend.modules.security import hash_password

logger = logging.getLogger("log_analyzer")

Base = declarative_base()


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="local")  # local | ad
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class GroupTemplate(Base):
    """Шаблон быстрого назначения групп AD, закреплённый за пользователем."""

    __tablename__ = "group_template"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    owner: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # JSON-список групп [{"name":..,"dn":..}, ...]
    groups: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppDB:
    def __init__(self) -> None:
        self.engine = None
        self.Session = None
        self.ready = False

    async def connect(self) -> None:
        url = (
            f"postgresql+asyncpg://{settings.app_db_user}:{settings.app_db_password}"
            f"@{settings.app_db_host}:{settings.app_db_port}/{settings.app_db_name}"
        )
        logger.info(
            "App DB connect: %s:%s/%s",
            settings.app_db_host,
            settings.app_db_port,
            settings.app_db_name,
        )
        self.engine = create_async_engine(url, pool_size=5, max_overflow=10)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await self._seed()
        self.ready = True

    async def dispose(self) -> None:
        if self.engine:
            await self.engine.dispose()

    async def _seed(self) -> None:
        async with self.Session() as s:
            count = (
                await s.execute(select(func.count()).select_from(AppUser))
            ).scalar_one()
            if not count:
                admin = AppUser(
                    username=str(settings.default_admin_user).strip().lower(),
                    password_hash=hash_password(settings.default_admin_password),
                    source="local",
                    is_admin=True,
                    must_change_password=True,
                )
                s.add(admin)
                logger.info(
                    "Создан дефолтный админ %r (требуется смена пароля)",
                    settings.default_admin_user,
                )

            # Первичный seed настройки группы входа из env (если ещё не задана).
            existing = (
                await s.execute(
                    select(AppSetting).where(AppSetting.key == "ad_login_group")
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(
                    AppSetting(key="ad_login_group", value=settings.ad_login_group or "")
                )

            await s.commit()

    # ---- users ----

    async def get_user(self, username: str) -> Optional[AppUser]:
        uname = str(username or "").strip().lower()
        if not uname:
            return None
        async with self.Session() as s:
            return (
                await s.execute(select(AppUser).where(AppUser.username == uname))
            ).scalar_one_or_none()

    async def set_password(
        self, username: str, new_password: str, *, must_change: bool = False
    ) -> bool:
        uname = str(username or "").strip().lower()
        async with self.Session() as s:
            user = (
                await s.execute(select(AppUser).where(AppUser.username == uname))
            ).scalar_one_or_none()
            if user is None:
                return False
            user.password_hash = hash_password(new_password)
            user.must_change_password = must_change
            await s.commit()
            return True

    # ---- settings ----

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self.Session() as s:
            row = (
                await s.execute(select(AppSetting).where(AppSetting.key == key))
            ).scalar_one_or_none()
            return row.value if row is not None else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.set_settings({key: value})

    async def get_all_settings(self) -> dict[str, str]:
        async with self.Session() as s:
            rows = (await s.execute(select(AppSetting))).scalars().all()
            return {r.key: r.value for r in rows}

    async def set_settings(self, values: dict[str, str]) -> None:
        async with self.Session() as s:
            for key, value in values.items():
                row = (
                    await s.execute(select(AppSetting).where(AppSetting.key == key))
                ).scalar_one_or_none()
                if row is None:
                    s.add(AppSetting(key=key, value=value or ""))
                else:
                    row.value = value or ""
            await s.commit()

    # ---- group templates (per-user) ----

    @staticmethod
    def _tpl_dict(row: "GroupTemplate") -> dict:
        try:
            groups = json.loads(row.groups or "[]")
        except Exception:
            groups = []
        if not isinstance(groups, list):
            groups = []
        return {"id": row.id, "name": row.name, "groups": groups}

    async def list_templates(self, owner: str) -> list[dict]:
        own = str(owner or "").strip().lower()
        if not own:
            return []
        async with self.Session() as s:
            rows = (
                await s.execute(
                    select(GroupTemplate)
                    .where(GroupTemplate.owner == own)
                    .order_by(GroupTemplate.name)
                )
            ).scalars().all()
            return [self._tpl_dict(r) for r in rows]

    async def create_template(
        self, owner: str, name: str, groups: list
    ) -> Optional[dict]:
        own = str(owner or "").strip().lower()
        nm = str(name or "").strip()
        if not own or not nm:
            return None
        async with self.Session() as s:
            row = GroupTemplate(
                owner=own,
                name=nm[:150],
                groups=json.dumps(groups or [], ensure_ascii=False),
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return self._tpl_dict(row)

    async def update_template(
        self, tid: int, owner: str, name: str, groups: list
    ) -> Optional[dict]:
        own = str(owner or "").strip().lower()
        nm = str(name or "").strip()
        async with self.Session() as s:
            row = (
                await s.execute(
                    select(GroupTemplate).where(
                        GroupTemplate.id == tid, GroupTemplate.owner == own
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if nm:
                row.name = nm[:150]
            row.groups = json.dumps(groups or [], ensure_ascii=False)
            await s.commit()
            await s.refresh(row)
            return self._tpl_dict(row)

    async def delete_template(self, tid: int, owner: str) -> bool:
        own = str(owner or "").strip().lower()
        async with self.Session() as s:
            row = (
                await s.execute(
                    select(GroupTemplate).where(
                        GroupTemplate.id == tid, GroupTemplate.owner == own
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True


app_db = AppDB()


async def init_app_db() -> bool:
    try:
        await app_db.connect()
        logger.info("[INIT] App DB: connected")
        return True
    except Exception:
        logger.exception(
            "App DB недоступна — авторизация работать не будет. Проверьте APP_DB_*"
        )
        return False
