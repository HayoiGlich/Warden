# Warden — Windows Logon Auditing & Active Directory Console

**English** · [Русский](README.ru.md)

A web console for security/IT teams that unifies **Windows event‑log search**,
**Active Directory account management**, and a couple of infrastructure tools
(Avanpost FAM presence checks, a Yandex Cloud VM cost report) behind a single
role‑based login.

Windows event collectors stay **native on the domain servers** and write events
into their own PostgreSQL databases. This web app runs in **Docker**, reads those
collector databases over the network (read‑only), and talks to Active Directory
under the signed‑in user's rights (delegation).

---

## Features

- **Event log search (Winlog)** — fan‑out search across multiple collector
  PostgreSQL databases, with live AD‑user autocomplete and group enrichment.
- **Active Directory management** — create / edit users one‑by‑one or in bulk
  (CSV/XLSX import), manage groups and OUs, generate logins and passwords,
  set account expiry, move between OUs.
- **Avanpost FAM check** — on the edit screen, shows whether an account made it
  into Avanpost FAM (with the reason if not) and a manual re‑sync button.
- **Yandex Cloud VM cost report** — pick which VMs to include, see the cost
  report in the browser (platform, CPU type, SSD/HDD split, snapshots), and
  download it as a filled‑in XLSX. Editable **tariffs** and server‑side caching
  with a manual refresh.
- **Services hub** — a shared page of links to internal services.
- **Auth & RBAC** — local admin + AD bind, cookie sessions, its own PostgreSQL
  app database; optional external **OIDC 2FA** login (e.g. Avanpost). Three
  roles: `admin` > `operator` > `viewer`.

## Architecture

```
[srv-1]  Windows: WEF + importer  ->  PostgreSQL #1  \
[srv-2]  Windows: WEF + importer  ->  PostgreSQL #2   >-- read over network --> [Docker] Warden web console
[srv-N]  ...                      ->  PostgreSQL #N  /                                |
                                                                                      +-- App PostgreSQL (auth + settings)
                                                                                      +-- Active Directory (LDAP/LDAPS)
```

- **Collectors** (`collector/`) run on Windows, ingest Windows Event Forwarding
  logs, and write to a per‑collector PostgreSQL. The web app never writes to them.
- **Web console** (`backend/` + `frontend-react/`) queries all collectors in
  parallel, tags each event with its collector, and merges the result.

See [DEPLOY.md](DEPLOY.md) for the collector/web split in detail.

## Tech stack

- **Backend** — Python 3.12, FastAPI, Uvicorn, SQLAlchemy + asyncpg (app DB),
  `ldap3` (AD), `aiohttp`, `openpyxl`, `yandexcloud`, `pyjwt`.
- **Frontend** — React 19 + Vite, React Router.
- **Databases** — PostgreSQL (app DB via Docker Compose; collector DBs external).
- **Deployment** — Docker + Docker Compose.

## Quick start (Docker)

```bash
git clone https://github.com/HayoiGlich/Warden.git && cd Warden

# 1. Configuration
cp .env.example .env
#    edit .env: DB_*, AD_*, SESSION_SECRET, DEFAULT_ADMIN_PASSWORD, ...

# 2. (optional) multiple collectors
cp collectors.example.json collectors.json   # then edit hosts/passwords

# 3. (optional) Yandex Cloud report — drop the SA key in place
#    yandex_mig/authorized_key.json  (see yandex_mig/authorized_key.example.json)

# 4. Build & run (web console + app PostgreSQL)
docker compose up -d --build
```

Open `http://<host>:8000` and sign in with `DEFAULT_ADMIN_USER` /
`DEFAULT_ADMIN_PASSWORD` from your `.env`.

## Configuration

| What | Where | Notes |
|------|-------|-------|
| App settings & secrets | `.env` | copy from `.env.example`; **never commit** |
| Collector databases | `collectors.json` | copy from `collectors.example.json`; falls back to `DB_*` in `.env` if absent |
| Yandex Cloud key | `yandex_mig/authorized_key.json` | service‑account key; **never commit** |
| Cost report template | `yandex_mig/Шаблон. Расчет ВМ цена. Общий.xlsx` | tariff defaults + report layout |

## Local development

**Backend**

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-web.txt
uvicorn main:app --reload --port 8000
```

The app needs a PostgreSQL for auth/settings. The simplest path is to run just
that service from Compose: `docker compose up -d db`.

**Frontend**

```bash
cd frontend-react
npm install
npm run dev        # dev server with proxy
npm run build      # production build into frontend-react/dist (served by the backend)
```

## Project structure

```
backend/            FastAPI app: api routers, services, AD/LDAP/DB modules
frontend-react/     React + Vite SPA (built into dist/, served by the backend)
collector/          Native Windows event-log importer (runs on domain servers)
yandex_mig/         Yandex Cloud helpers + VM cost report template
main.py             App entry point (FastAPI + static SPA)
Dockerfile          Multi-stage build (frontend -> Python runtime)
docker-compose.yml  Web console + app PostgreSQL
DEPLOY.md           Collector/web split & deployment notes
```

## Security notes

- `.env`, `authorized_key.json`, and `collectors.json` are **git‑ignored** — keep
  it that way; they contain passwords and keys.
- Change `SESSION_SECRET` (random) and `DEFAULT_ADMIN_PASSWORD` before any real use.
- AD account creation and password changes require an encrypted channel
  (`AD_USE_SSL=1` for LDAPS or `AD_START_TLS=1`).
- The web console only reads collector databases; it never writes to them.

## License

No license specified yet — add one (e.g. `LICENSE` file) before publishing if you
intend others to reuse the code.
