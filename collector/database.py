from datetime import datetime
from typing import Optional, Any, List, Sequence

from sqlalchemy import Integer, Text, String, DateTime, ARRAY, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
import logging

Base = declarative_base()
logger = logging.getLogger("log_analyzer")


class LoginEvent(Base):
    __tablename__ = "logins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Основные
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    time_created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    computer: Mapped[Optional[str]] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    logon_type: Mapped[Optional[str]] = mapped_column(String(50))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    workstation_name: Mapped[Optional[str]] = mapped_column(String(255))
    target_domain: Mapped[Optional[str]] = mapped_column(String(255))
    groups: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String))

    message: Mapped[Optional[str]] = mapped_column(Text)

    # Event metadata
    source_name: Mapped[Optional[str]] = mapped_column(String(255))
    task_category: Mapped[Optional[str]] = mapped_column(String(255))
    level: Mapped[int] = mapped_column(Integer, default=0)
    keywords: Mapped[Optional[str]] = mapped_column(String(255))
    event_record_id: Mapped[int] = mapped_column(Integer, default=0)
    activity_id: Mapped[Optional[str]] = mapped_column(String(255))

    process_id: Mapped[int] = mapped_column(Integer, default=0)
    thread_id: Mapped[int] = mapped_column(Integer, default=0)

    channel: Mapped[Optional[str]] = mapped_column(String(255))
    provider_name: Mapped[Optional[str]] = mapped_column(String(255))
    provider_guid: Mapped[Optional[str]] = mapped_column(String(255))

    # Security / Logon
    target_logon_id: Mapped[Optional[str]] = mapped_column(String(255))
    subject_user_name: Mapped[Optional[str]] = mapped_column(String(255))
    subject_domain_name: Mapped[Optional[str]] = mapped_column(String(255))
    subject_logon_id: Mapped[Optional[str]] = mapped_column(String(255))

    status: Mapped[Optional[str]] = mapped_column(String(50))
    sub_status: Mapped[Optional[str]] = mapped_column(String(50))

    logon_id: Mapped[Optional[str]] = mapped_column(String(255))
    authentication_package: Mapped[Optional[str]] = mapped_column(String(255))
    transmitted_services: Mapped[Optional[str]] = mapped_column(String(255))
    package_name: Mapped[Optional[str]] = mapped_column(String(255))
    failure_reason: Mapped[Optional[str]] = mapped_column(String(255))
    impersonation_level: Mapped[Optional[str]] = mapped_column(String(255))
    restricted_admin_mode: Mapped[Optional[str]] = mapped_column(String(255))
    virtual_account: Mapped[Optional[str]] = mapped_column(String(255))
    elevated_token: Mapped[Optional[str]] = mapped_column(String(255))

    # Raw data
    xml_content: Mapped[Optional[str]] = mapped_column(Text)
    parsed_data: Mapped[Optional[str]] = mapped_column(Text)
    evtx_file: Mapped[Optional[str]] = mapped_column(String(500))

    # Service
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Database:
    def __init__(self) -> None:
        self.engine = None
        self.Session: Any = None

    async def connect(self, user, password, host, database, port=5432, create_tables=True):
        logger.info(
            "DB connect: host=%s port=%s db=%s user=%s", host, port, database, user
        )
        self.engine = create_async_engine(
            f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}",
            echo=False,
            pool_size=5,
            max_overflow=10,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        # Веб-«управлялка» только читает чужие БД коллекторов — DDL ей не нужен
        # (и часто запрещён правами). Таблицы создаёт сам коллектор.
        if create_tables:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("[DB] Таблицы готовы")
        else:
            # Лёгкая проверка доступности соединения.
            async with self.engine.connect() as conn:
                await conn.exec_driver_sql("SELECT 1")

    def _build_conditions(
        self,
        *,
        username: str | None,
        usernames: Sequence[str] | None,
        computer: str | None,
        date_from: datetime | None,
    ):
        conditions = []

        # usernames (список) — строгий IN по lower
        if usernames:
            lowered = [u.strip().lower() for u in usernames if (u or "").strip()]
            if lowered:
                conditions.append(func.lower(LoginEvent.username).in_(lowered))
            else:
                # пустой список => ничего не найдём
                conditions.append(False)

        # username (строка) — как раньше, fuzzy LIKE
        elif username:
            u = username.strip().lower()
            if u:
                conditions.append(func.lower(LoginEvent.username).like(f"%{u}%"))

        if computer:
            c = computer.strip().lower()
            if c:
                conditions.append(func.lower(LoginEvent.computer).like(f"%{c}%"))

        if date_from:
            conditions.append(LoginEvent.time_created >= date_from)

        return conditions

    async def count_event(
        self,
        *,
        username: str | None = None,
        usernames: Sequence[str] | None = None,
        computer: str | None = None,
        date_from: datetime | None = None,
    ) -> int:
        if self.Session is None:
            raise RuntimeError("Database not initialized")

        async with self.Session() as session:
            conditions = self._build_conditions(
                username=username,
                usernames=usernames,
                computer=computer,
                date_from=date_from,
            )

            stmt = select(func.count()).select_from(LoginEvent).where(*conditions)
            value = await session.execute(stmt)
            total = value.scalar_one()  # int

        return int(total or 0)

    async def fetch_event(
        self,
        username: str | None,
        computer: str | None,
        date_from: datetime | None = None,
        *,
        usernames: Sequence[str] | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        if self.Session is None:
            raise RuntimeError("Database not initialized")

        limit = max(1, min(int(limit), 5000))
        offset = max(0, int(offset))

        async with self.Session() as session:
            conditions = self._build_conditions(
                username=username,
                usernames=usernames,
                computer=computer,
                date_from=date_from,
            )

            stmt = (
                select(LoginEvent)
                .where(*conditions)
                .order_by(LoginEvent.time_created.desc())
                .limit(limit)
                .offset(offset)
            )

            rows = (await session.execute(stmt)).scalars().all()

        return [
            {
                "id": r.id,
                "event_id": r.event_id,
                "time_created": r.time_created.isoformat() if r.time_created else None,
                "computer": r.computer,
                "username": (r.username or "").strip()
                or (r.subject_user_name or "").strip(),
                "logon_type": r.logon_type,
                "ip_address": r.ip_address,
                "workstation_name": r.workstation_name,
                "target_domain": r.target_domain,
                "groups": r.groups or [],
                "message": r.message or "",
                "status": r.status,
                "failure_reason": r.failure_reason,
                "authentication_package": r.authentication_package,
                "process_id": r.process_id,
                "thread_id": r.thread_id,
                "subject_user_name": r.subject_user_name,
                "subject_domain_name": r.subject_domain_name,
            }
            for r in rows
        ]
