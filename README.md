# Nexus Sales

Monorepo **Nexus Sales** con frontend (React + Vite + Tailwind) y backend (FastAPI + SQLite + SQLAlchemy).

**Fase 2:** empresas, usuarios por rol, productos y créditos (wallet + asignaciones).

**Fase 3:** campañas comerciales (ICP, estimaciones de reuniones/costos simulados en backend, UI de listado/detalle y calculadora previa). Sin integraciones LinkedIn / Gmail / OpenAI / Calendar reales.

**Fase 4:** prospectos por campaña (alta manual, bulk JSON, simulación, scoring local vs ICP, dedupe por `linkedin_url` o nombre+empresa sin LinkedIn). El endpoint bulk está pensado como receptor de futuras cargas desde extensión de Chrome sobre LinkedIn; no hay scraping ni API de LinkedIn aún.

## Requisitos (Windows)

- **Node.js** LTS (incluye `npm`)
- **Python** 3.11+ recomendado (probado también con 3.13)
- **pip** actualizado

## Backend (FastAPI)

Desde la raíz del repositorio, en PowerShell o CMD:

1. `cd backend`
2. `pip install -r requirements.txt`  
   *(Incluye `email-validator` para `EmailStr` en Pydantic.)*
3. `python -m uvicorn app.main:app --reload`

*(Recomendado: entorno virtual `python -m venv .venv` y activarlo.)*

La API queda en `http://127.0.0.1:8000`.

- **Health:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- **Documentación:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Base de datos (SQLite)

- Archivo por defecto: `backend/data/nexus_sales.db` (se crean carpeta y tablas al arrancar con `lifespan`).
- **Sin Alembic (por ahora):** las tablas se generan con `create_all`.
- Si venías de la **fase 1** y el SQLite no tiene las tablas nuevas, o aparece `"no such table: companies"`:
  1. Pará uvicorn.
  2. Borrá `backend/data/nexus_sales.db` (y la carpeta `data` si querés limpiar del todo).
  3. Volvé a levantar el backend: se creará el archivo y correra el **seed demo** una sola vez (base vacía).

### Seed demo (automático)

Al iniciar sin datos, se crea:

- Empresa **CostGuard Demo Client**
- 1 **admin**, 1 **manager**, 3 **vendedores**
- **2 productos**
- **Wallet** con **500 USD** de créditos totales (enteros)
- **Asignaciones** iniciales a los vendedores (suma asignada 400 USD; quedan **100 USD** sin asignar como demo)

Si la empresa demo ya existe, el seed **no duplica**.

## Frontend (Vite + React + Tailwind)

1. `cd frontend`
2. `npm install`
3. `npm run dev`

Abre la URL que indique Vite (`http://localhost:5173`).

Opcional: `frontend/.env`:

```env
VITE_API_URL=http://127.0.0.1:8000
```

Empresa seleccionada: se usa la primera empresa del API si no hay nada guardado en `localStorage` (`nexus_sales_company_id`). En el header verás nombre o selector si hay más de una.

## Flujos rápidos (QA manual)

Arrancá backend + frontend. Abre **Productos**:

- Crear/editar/desactivar (soft delete) producto desde la tabla y el modal.

**Caja / Créditos**:

- «Simular carga de saldo» → `POST /wallet/top-up`
- «Asignar a vendedor» → debe bloquearse en UI si el monto es mayor que el disponible sin asignar; el backend responde 400 `"Saldo no asignado insuficiente"` si se fuerza.

**Equipo**, **Dashboard** y **Campañas** consumen el mismo API local.

**Campañas (v0.3)**

- Listado: `/campanas`
- Detalle: `/campanas/{id}` · estimaciones solo como rangos (no promesas exactas)
- Verificación rápida backend: `POST /companies/{id}/campaigns/preview-estimates` con `{"prospect_count": N}`

La tabla `campaigns` se crea sola con `create_all` si el SQLite ya existía de fases anteriores.

### CORS

Orígenes permitidos: `http://localhost:5173` y `http://127.0.0.1:5173`.

## Estructura del repositorio

```
Nexus-sales/
  frontend/
  backend/
    app/
      models/
      schemas/
      routes/
      services/
      database/
    data/          # .db generado (no versionar)
  README.md
```

## Siguientes ideas (fuera de fase 2)

- Migraciones formales (**Alembic**)
- Autenticación y RBAC persistente por sesión
- Integraciones (OpenAI, Gmail, Calendar) y extensión Chrome en paquete aparte
