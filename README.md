# Nexus Sales

Plataforma comercial B2B para equipos SDR: campañas, prospectos, secuencias multicanal (email, LinkedIn, WhatsApp), reuniones y métricas de equipo.

Monorepo con **frontend** (React + Vite + Tailwind) y **backend** (FastAPI + SQLite + SQLAlchemy).

## Qué incluye hoy

| Área | Estado |
|------|--------|
| Login JWT y roles (SDR, Manager, Gerente) | Operativo |
| Campañas, prospectos, pipeline | Operativo |
| Secuencia 21 días (Gmail, LinkedIn asistido, WhatsApp Cloud) | Operativo |
| Google OAuth (Gmail + Calendar por usuario) | Operativo |
| WhatsApp Business Cloud API | **Pendiente Meta** — MVP omite D7/D16; ver `docs/WHATSAPP_SETUP.md` |
| Extensión Chrome LinkedIn Assist | Ver `browser-extension/TEAM_INSTALL.md` |
| Centro de Operaciones (manager/gerente) | Operativo |
| Lead sourcing (Brave / Prospeo / Phantom experimental) | Opcional por API keys |
| HubSpot CRM (sync contactos + notas) | v1 con Private App token |
| Salesforce CRM (sync contactos + tareas) | Operativo con Connected App + refresh token |
| Deploy Docker (staging) | `docker-compose.yml` + `docs/PILOT_CHECKLIST.md` |

## Requisitos

- **Node.js** LTS (incluye `npm`)
- **Python** 3.11+ (probado también con 3.13)
- Windows, macOS o Linux

## Desarrollo local (rápido)

Piloto / staging con Docker: ver **`docs/PILOT_CHECKLIST.md`**.

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

- API: [http://127.0.0.1:8002](http://127.0.0.1:8002)
- Health: [http://127.0.0.1:8002/health](http://127.0.0.1:8002/health)
- OpenAPI: [http://127.0.0.1:8002/docs](http://127.0.0.1:8002/docs)

SQLite se crea en `backend/data/nexus_sales.db` al primer arranque (`create_all`, sin Alembic por ahora).

### 2. Frontend

```powershell
cd frontend
npm install
copy .env.example .env
npm run dev
```

Abre la URL de Vite (por defecto [http://localhost:5173](http://localhost:5173)).

App interna **Nexus Support** (no es Sales): `cd nexus-support && npm install && npm run dev` → [http://127.0.0.1:5174](http://127.0.0.1:5174). Ver [docs/NEXUS_SUPPORT.md](docs/NEXUS_SUPPORT.md).

El frontend usa **proxy de Vite** hacia el backend en dev (`VITE_API_URL` en `.env` debe apuntar al mismo puerto que uvicorn, típicamente **8002**).

### 3. Login demo

Tras el seed automático (base vacía), usá:

| Rol | Email | Contraseña |
|-----|-------|------------|
| SDR | `sdr@test.com` | `demo123` |
| Manager | `manager@test.com` | `demo123` |
| Gerente | `director@test.com` | `demo123` |

Empresa demo: **CostGuard Demo Client**.

Para omitir el seed: `NEXUS_SKIP_DEMO_SEED=1` en `backend/.env`. Con `NEXUS_REAL_MODE=1` el seed también se omite.

## Variables de entorno mínimas (dev)

Copiá plantillas:

- `backend/.env.example` → `backend/.env`
- `frontend/.env.example` → `frontend/.env`

Para probar secuencias reales en local sin simulación:

```env
NEXUS_REAL_MODE=1
NEXUS_ENABLE_SEQUENCE_TESTING=1
OPENAI_API_KEY=sk-...
```

Gmail/Calendar: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `NEXUS_TOKEN_FERNET_KEY`, `NEXUS_FRONTEND_URL`.

WhatsApp sin Meta aún: `WHATSAPP_DRY_RUN=1` — ver [docs/WHATSAPP_SETUP.md](docs/WHATSAPP_SETUP.md).

Lista completa y comentarios: `backend/.env.example`.

## Flujos útiles en la UI

- **Consola** (`/dashboard`) — resumen del equipo
- **Contactar** — outreach y cola LinkedIn
- **Campañas** — ICP, secuencia, autopilot
- **Prospectos** — detalle, historial de toques, ejecutar día N
- **Configuración → Integraciones** — Google, WhatsApp, extensión LinkedIn
- **Operaciones** (`/operaciones`) — solo manager/gerente: salud WhatsApp, scheduler, modo real

### Scripts de prueba (backend)

```powershell
cd backend
python scripts/setup_test_linkedin_d4.py   # prospecto listo para Día 4 LinkedIn
python scripts/setup_test_whatsapp_d7.py   # prospecto listo para Día 7 WhatsApp
```

## Tests y CI

### Local

```powershell
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

```powershell
cd frontend
npm run build
```

### GitHub Actions

Workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Backend:** `pytest` (105+ tests unitarios, sin API keys externas)
- **Frontend:** `npm run build`

Se dispara en push/PR a `main` o `master`.

`npm run lint` existe pero **no bloquea CI** todavía (reglas estrictas de React 19 pendientes de limpieza).

## Estructura del repo

```
Proyecto-J/
  frontend/           # Nexus Sales (comercial)
  nexus-support/      # Nexus Support (app interna, puerto 5174)
  backend/
    app/              # FastAPI, modelos, servicios
    data/             # SQLite (no versionar)
    scripts/          # utilidades de QA
  browser-extension/  # LinkedIn Assist
  docs/
    PRODUCTION.md     # deploy y checklist
    WHATSAPP_SETUP.md
  scripts/            # build manifest extensión, pack ZIP
  dist/               # nexus-linkedin-assist.zip (generado, no versionar)
```

## Documentación

| Doc | Contenido |
|-----|-----------|
| [docs/PRODUCTION.md](docs/PRODUCTION.md) | Deploy, seguridad, CORS, scheduler |
| [docs/NEXUS_SUPPORT.md](docs/NEXUS_SUPPORT.md) | App interna Nexus Support (aparte de Sales) |
| [docs/CRM_INTEGRATIONS.md](docs/CRM_INTEGRATIONS.md) | HubSpot + Salesforce |
| [docs/WHATSAPP_SETUP.md](docs/WHATSAPP_SETUP.md) | Dry run, Meta, go-live |
| [browser-extension/TEAM_INSTALL.md](browser-extension/TEAM_INSTALL.md) | Extensión LinkedIn para el equipo |

Empaquetar extensión: `node scripts/pack-extension.mjs` → `dist/nexus-linkedin-assist.zip`

## CORS

En desarrollo: `localhost:5173` y `127.0.0.1:5173` (más regex local).

En producción: definí `NEXUS_FRONTEND_URL` y/o `NEXUS_CORS_ORIGINS` (CSV). Detalle en [docs/PRODUCTION.md](docs/PRODUCTION.md).

## Roadmap cercano

- Verificación Meta / WhatsApp real (E2E Día 7)
- HubSpot Salesforce OAuth por empresa en UI
- Migraciones Alembic
- ESLint en CI (limpiar ~70 avisos React 19)
