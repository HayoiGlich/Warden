# syntax=docker/dockerfile:1

# =========================================================
# Stage 1 — сборка React-фронтенда
# =========================================================
FROM node:20-alpine AS frontend
WORKDIR /app/frontend-react

COPY frontend-react/package.json frontend-react/package-lock.json ./
RUN npm ci

COPY frontend-react/ ./
RUN npm run build

# =========================================================
# Stage 2 — Python-рантайм веб-«управлялки»
# =========================================================
FROM python:3.12-slim AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

# Бэкенд + точка входа + конфиг логирования
COPY backend/ ./backend/
COPY main.py logging_config.yaml ./

# Собранный SPA из stage 1
COPY --from=frontend /app/frontend-react/dist ./frontend-react/dist

# Непривилегированный пользователь + каталог логов с правами на запись
RUN useradd -r -u 10001 appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health-проверка: приложение поднялось и отвечает (БД/AD могут быть down — это ок)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"

# .env подаётся через env_file в docker-compose (секреты не зашиваем в образ).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
