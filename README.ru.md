# Warden — аудит входов Windows и управление Active Directory

[English](README.md) · **Русский**

Веб‑консоль для ИБ/ИТ‑команд, объединяющая **поиск по журналам Windows**,
**управление учётными записями Active Directory** и пару инфраструктурных
инструментов (проверка попадания в Avanpost FAM, отчёт по стоимости ВМ в
Yandex Cloud) под единым входом с ролями.

Сборщики событий Windows остаются **нативными на серверах домена** и пишут
события в свои базы PostgreSQL. Это веб‑приложение работает в **Docker**, читает
базы сборщиков по сети (только чтение) и обращается к Active Directory под
правами вошедшего пользователя (делегирование).

---

## Возможности

- **Поиск по журналам (Winlog)** — параллельный поиск сразу по нескольким базам
  сборщиков, живой автокомплит пользователей AD и обогащение группами.
- **Управление Active Directory** — создание/редактирование учёток по одной или
  массово (импорт CSV/XLSX), управление группами и OU, генерация логинов и
  паролей, срок действия учётки, перенос между OU.
- **Проверка Avanpost FAM** — на экране редактирования показывает, попала ли
  учётка в Avanpost FAM (с причиной, если нет), и кнопку ручной синхронизации.
- **Отчёт по стоимости ВМ Yandex Cloud** — выбор машин для отчёта, просмотр
  в браузере (платформа, тип ЦПУ, раздельно SSD/HDD, снимки) и выгрузка готового
  **XLSX**. Редактируемые **тарифы** и кэш на стороне сервера с кнопкой «Обновить».
- **Хаб сервисов** — общая страница со ссылками на внутренние сервисы.
- **Аутентификация и роли** — локальный админ + вход по AD, cookie‑сессии,
  собственная база PostgreSQL приложения; опциональный внешний вход **OIDC 2FA**
  (например, Avanpost). Три роли: `admin` > `operator` > `viewer`.

## Архитектура

```
[srv-1]  Windows: WEF + импортёр  ->  PostgreSQL #1  \
[srv-2]  Windows: WEF + импортёр  ->  PostgreSQL #2   >-- чтение по сети --> [Docker] веб-консоль Warden
[srv-N]  ...                      ->  PostgreSQL #N  /                              |
                                                                                    +-- PostgreSQL приложения (авторизация + настройки)
                                                                                    +-- Active Directory (LDAP/LDAPS)
```

- **Сборщики** (`collector/`) работают на Windows, забирают журналы через
  Windows Event Forwarding и пишут в свою PostgreSQL. Веб‑часть в них не пишет.
- **Веб‑консоль** (`backend/` + `frontend-react/`) опрашивает все сборщики
  параллельно, помечает каждое событие именем сборщика и сливает результат.

Подробнее про разделение сборщик/веб — в [DEPLOY.md](DEPLOY.md).

## Стек технологий

- **Бэкенд** — Python 3.12, FastAPI, Uvicorn, SQLAlchemy + asyncpg (база
  приложения), `ldap3` (AD), `aiohttp`, `openpyxl`, `yandexcloud`, `pyjwt`.
- **Фронтенд** — React 19 + Vite, React Router.
- **Базы данных** — PostgreSQL (база приложения через Docker Compose; базы
  сборщиков внешние).
- **Развёртывание** — Docker + Docker Compose.

## Быстрый старт (Docker)

```bash
git clone https://github.com/HayoiGlich/Warden.git && cd Warden

# 1. Конфигурация
cp .env.example .env
#    отредактируйте .env: DB_*, AD_*, SESSION_SECRET, DEFAULT_ADMIN_PASSWORD, ...

# 2. (опционально) несколько сборщиков
cp collectors.example.json collectors.json   # затем впишите хосты/пароли

# 3. (опционально) отчёт Yandex Cloud — положите ключ сервисного аккаунта
#    yandex_mig/authorized_key.json  (формат — в yandex_mig/authorized_key.example.json)

# 4. Сборка и запуск (веб-консоль + PostgreSQL приложения)
docker compose up -d --build
```

Откройте `http://<хост>:8000` и войдите под `DEFAULT_ADMIN_USER` /
`DEFAULT_ADMIN_PASSWORD` из вашего `.env`.

## Конфигурация

| Что | Где | Примечание |
|-----|-----|------------|
| Настройки и секреты | `.env` | копируется из `.env.example`; **не коммитить** |
| Базы сборщиков | `collectors.json` | копируется из `collectors.example.json`; при отсутствии — берётся `DB_*` из `.env` |
| Ключ Yandex Cloud | `yandex_mig/authorized_key.json` | ключ сервисного аккаунта; **не коммитить** |
| Шаблон отчёта | `yandex_mig/Шаблон. Расчет ВМ цена. Общий.xlsx` | тарифы по умолчанию + разметка отчёта |

## Локальная разработка

**Бэкенд**

```bash
python -m venv .venv && .venv\Scripts\activate   # Linux/macOS: . .venv/bin/activate
pip install -r requirements-web.txt
uvicorn main:app --reload --port 8000
```

Приложению нужна PostgreSQL для авторизации/настроек. Проще всего поднять только
эту службу из Compose: `docker compose up -d db`.

**Фронтенд**

```bash
cd frontend-react
npm install
npm run dev        # dev-сервер с проксированием
npm run build      # прод-сборка в frontend-react/dist (её отдаёт бэкенд)
```

## Структура проекта

```
backend/            FastAPI: роутеры, сервисы, модули AD/LDAP/БД
frontend-react/     SPA на React + Vite (собирается в dist/, отдаётся бэкендом)
collector/          Нативный импортёр журналов Windows (на серверах домена)
yandex_mig/         Хелперы Yandex Cloud + шаблон отчёта по стоимости ВМ
main.py             Точка входа (FastAPI + отдача SPA)
Dockerfile          Многоэтапная сборка (фронтенд -> Python-рантайм)
docker-compose.yml  Веб-консоль + PostgreSQL приложения
DEPLOY.md           Разделение сборщик/веб и заметки по развёртыванию
```

## Безопасность

- `.env`, `authorized_key.json` и `collectors.json` **в `.gitignore`** — так и
  оставьте: там пароли и ключи.
- Смените `SESSION_SECRET` (случайный) и `DEFAULT_ADMIN_PASSWORD` до реального
  использования.
- Создание учёток и смена паролей в AD требуют зашифрованного канала
  (`AD_USE_SSL=1` для LDAPS или `AD_START_TLS=1`).
- Веб‑консоль только читает базы сборщиков и никогда в них не пишет.

## Лицензия

Лицензия пока не указана — добавьте её (например, файл `LICENSE`) перед
публикацией, если планируете, что другие будут использовать код.
