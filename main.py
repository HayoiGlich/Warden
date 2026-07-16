from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import logging.config
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

load_dotenv()

from backend.api.routes import router as api_router
from backend.api.ad_admin_router import ad_admin_router
from backend.api.auth_router import auth_router
from backend.api.oidc_router import oidc_router, oidc_callback
from backend.api.templates_router import templates_router
from backend.api.settings_router import settings_router
from backend.api.yc_router import yc_router
from backend.api.auth_middleware import AuthMiddleware
from backend.modules.config import settings
from backend.modules.collector_pool import CollectorPool
from backend.modules.app_db import app_db, init_app_db
from backend.modules.runtime_settings import runtime_settings
from backend.modules.runtime_collectors import runtime_collectors
from backend.modules.ldap_providers import ldap_providers
from backend.modules.role_mappings import role_mappings
from backend.modules.service_links import service_links
from backend.modules.attr_mapping import attr_mapping
from backend.modules.yc_tariff import yc_tariff
from backend.modules.ad_connector import init_ad_connector, get_ad_connector


logger = logging.getLogger("log_analyzer")

# Пустой пул: реальный список коллекторов грузится в lifespan из БД настроек
collectors = CollectorPool(configs=[])


def setup_logging() -> None:
    cfg_path = Path("logging_config.yaml")
    if cfg_path.is_file():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        # Создаём каталоги для file-хендлеров: RotatingFileHandler сам каталог
        # не создаёт, а в свежем контейнере папки logs/ ещё нет.
        for handler in (cfg.get("handlers") or {}).values():
            filename = handler.get("filename") if isinstance(handler, dict) else None
            if filename:
                Path(filename).parent.mkdir(parents=True, exist_ok=True)
        logging.config.dictConfig(cfg)
    else:
        logging.basicConfig(level=logging.INFO)


def frontend_dir() -> Path:
    return Path("frontend").resolve()


def react_frontend_dir() -> Path:
    return Path("frontend-react").resolve() / "dist"


def react_frontend_ready() -> bool:
    return (react_frontend_dir() / "index.html").is_file()


def file_or_404(path: Path) -> FileResponse:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return FileResponse(str(path))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("=== Starting service ===")

    # App DB (авторизация + настройки) — сид дефолтного админа
    await init_app_db()

    # Runtime-настройки (AD-конфиг из БД поверх .env) — до инициализации AD
    await runtime_settings.load()

    # Реестр LDAP-провайдеров (активный питает вход/анализатор/AD) — до AD init
    await ldap_providers.load()

    # Соответствие «группа AD → роль» — до входа (роль считается при login)
    await role_mappings.load()

    # Маппинг атрибутов профиля (заполняется при входе) и хаб ссылок-сервисов
    await attr_mapping.load()
    await service_links.load()

    # Тарифы YC (цены за час для расчёта стоимости ВМ) — из БД, дефолт из шаблона
    await yc_tariff.load()

    # Реестр коллекторов (из БД настроек, fallback env/файл) — до подключения
    await runtime_collectors.load()

    # AD init
    ad_connected = False
    if not settings.disable_ad:
        ad_connected = init_ad_connector()
    logger.info(
        "[INIT] AD: %s", "connected" if ad_connected else "disabled/disconnected"
    )

    # Коллекторы (несколько БД) — подключаемся ко всем, недоступные не валят старт
    await collectors.reload(runtime_collectors.get_configs())
    app.state.collectors = collectors

    yield

    # shutdown
    ad = get_ad_connector()
    if ad:
        ad.disconnect()

    await collectors.dispose_all()
    await app_db.dispose()

    logger.info("=== Service stopped ===")


app = FastAPI(title="Windows Log Analyzer API", lifespan=lifespan)

# Порядок важен: add_middleware добавляет «наружу», последний add — самый
# внешний. Нужно: CORS -> Session -> Auth -> роут. Поэтому CORS добавляем
# последним, а Auth — первым.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=settings.session_cookie,
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=False,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# API router
app.include_router(api_router)
app.include_router(ad_admin_router)
app.include_router(auth_router)
app.include_router(oidc_router)
app.include_router(templates_router)
app.include_router(settings_router)
app.include_router(yc_router)

if react_frontend_ready() and (react_frontend_dir() / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(react_frontend_dir() / "assets"), html=False),
        name="assets",
    )
elif os.path.isdir("frontend"):
    app.mount("/assets", StaticFiles(directory="frontend", html=False), name="assets")


@app.get("/")
async def page_root():
    if react_frontend_ready():
        return file_or_404(react_frontend_dir() / "index.html")
    return file_or_404(frontend_dir() / "index.html")


@app.get("/winlog")
async def page_winlog():
    if react_frontend_ready():
        return file_or_404(react_frontend_dir() / "index.html")
    return file_or_404(frontend_dir() / "winlog" / "index.html")


@app.get("/callback", include_in_schema=False)
async def page_oidc_callback_alias(request: Request):
    """Короткий алиас OIDC-callback.

    Канонический путь — /api/auth/oidc/callback, но в IdP redirect_uri часто
    регистрируют как <host>/callback. Без этого алиаса такой путь попадал бы в
    SPA-fallback (отдавался index.html), и код авторизации терялся.
    """
    return await oidc_callback(request)


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """SPA-fallback: любые клиентские маршруты (/ad-users, /settings…) при
    прямом заходе/обновлении страницы отдают index.html — роутинг делает
    React. API-пути сюда попадать не должны — для них корректный JSON-404.
    """
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")

    if react_frontend_ready():
        dist = react_frontend_dir()
        # Реальный статический файл (favicon, vite.svg и т.п.) — отдаём как есть.
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(dist)):
                return FileResponse(str(candidate))
        return file_or_404(dist / "index.html")
    return file_or_404(frontend_dir() / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.log_level.lower(),
        reload=True,
    )
