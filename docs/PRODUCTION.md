# Producción — Nexus Sales

Guía para deploy demo o producción. Asume un dominio propio (`app.tuempresa.com` + `api.tuempresa.com` o mismo host con reverse proxy).

## Arquitectura mínima

```
[Navegador SDR]
    → Frontend estático (dist/)  — VITE_API_URL apunta al API
    → Extensión Chrome (LinkedIn) — content_scripts con URL del frontend
    → Backend FastAPI (uvicorn)  — SQLite o futuro Postgres
         → OpenAI (mensajes de secuencia / inbound)
         → Google OAuth (Gmail + Calendar por usuario)
         → Meta Graph (WhatsApp Cloud)
```

No hay workers separados: el **scheduler** corre dentro del proceso uvicorn si `NEXUS_AUTOMATION_SCHEDULER=1`.

## Checklist pre-deploy

### Backend

1. `cp backend/.env.example backend/.env` (o equivalente en el servidor).
2. Configurá **obligatorio en prod**:

| Variable | Notas |
|----------|--------|
| `NEXUS_REAL_MODE=1` | Sin simulaciones de outreach en BD |
| `NEXUS_SKIP_DEMO_SEED=1` | No crear CostGuard Demo |
| `NEXUS_JWT_SECRET` | String largo aleatorio (no el default de dev) |
| `NEXUS_TOKEN_FERNET_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `OPENAI_API_KEY` | Generación de mensajes SDR |
| `NEXUS_FRONTEND_URL` | URL pública del SPA, sin barra final |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth SDR |
| `GOOGLE_REDIRECT_URI` | Debe coincidir con la consola Google (ej. `https://api.tuempresa.com/auth/google/callback`) |

3. Configurá **según canales**:

| Variable | Cuándo |
|----------|--------|
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp real |
| `WHATSAPP_DRY_RUN=1` | Solo pruebas de flujo sin Meta |
| `HUBSPOT_ACCESS_TOKEN` | Sync CRM HubSpot (contactos + notas) |
| `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_REFRESH_TOKEN`, `SALESFORCE_INSTANCE_URL` | Sync CRM Salesforce |
| `NEXUS_CORS_ORIGINS` | Si frontend y API están en orígenes distintos (CSV) |

4. **Recomendado al inicio**:

```env
NEXUS_AUTO_SEND_ENABLED=0
NEXUS_AUTOMATION_SCHEDULER=0
ENABLE_GMAIL_AUTOMATION=0
```

Activá scheduler y auto-send cuando validaste OAuth, límites y plantillas en staging.

5. Arranque:

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8002
```

En producción usá un process manager (systemd, Docker, PM2) y HTTPS terminado en nginx/Caddy.

### Docker Compose (staging / demo)

Stack mínimo: API + SPA con nginx (mismo origen, sin CORS). WhatsApp no es requerido.

```bash
cp .env.production.example .env.production
# Completar secretos (JWT, Fernet, OpenAI, Google OAuth)

docker compose --env-file .env.production up -d --build
```

- App: `http://localhost:8080` (registro en `/registro` si `NEXUS_ALLOW_WORKSPACE_SIGNUP=1`)
- API directa: `http://localhost:8002/health`
- Datos SQLite en volumen Docker `nexus_data`

Checklist del deploy: **`docs/DEPLOY_CHECKLIST.md`**.

### Frontend

1. `frontend/.env`:

```env
VITE_API_URL=https://api.tuempresa.com
```

2. Build y publicación:

```bash
cd frontend
npm ci
npm run build
```

Serví `frontend/dist/` como sitio estático. El SPA usa history API: todas las rutas deben caer en `index.html`.

Ejemplo nginx (fragmento):

```nginx
server {
    listen 443 ssl;
    server_name app.tuempresa.com;
    root /var/www/nexus/dist;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

API en otro server block o subdominio con proxy a `127.0.0.1:8002`.

### Extensión LinkedIn

1. Desde la raíz del repo:

```bash
# Dev (localhost)
node scripts/pack-extension.mjs

# Producción
NEXUS_FRONTEND_URL=https://app.tuempresa.com node scripts/pack-extension.mjs
```

2. Distribuí `dist/nexus-linkedin-assist.zip` al equipo.
3. Cada SDR: cargar descomprimida en Chrome y verificar en **Configuración → Integraciones**.

Detalle: [browser-extension/TEAM_INSTALL.md](../browser-extension/TEAM_INSTALL.md).

### WhatsApp

- Sin Meta aprobado: `WHATSAPP_DRY_RUN=1`
- Con Meta: quitar dry run y configurar token + Phone Number ID

Guía completa: [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md)

Verificación:

- `GET /health/whatsapp?deep=true`
- UI: Integraciones → WhatsApp → «Verificar API»

## CORS

El backend permite:

- Orígenes en `NEXUS_CORS_ORIGINS` (CSV), o
- Por defecto: localhost + `NEXUS_FRONTEND_URL`
- Regex adicional para hosts locales en dev

En prod, si el SPA y el API comparten dominio vía proxy (mismo origin), CORS es menos crítico; si están en subdominios distintos, **definí `NEXUS_CORS_ORIGINS` explícitamente**.

## Automatización en servidor

Variables relevantes (ver comentarios en `.env.example`):

| Variable | Efecto |
|----------|--------|
| `NEXUS_AUTOMATION_SCHEDULER=1` | Jobs periódicos en uvicorn |
| `ENABLE_GMAIL_AUTOMATION=1` | Poll Gmail, sync calendar, inbound auto-reply |
| `NEXUS_AUTO_SEND_ENABLED=1` | Envío automático de emails (requiere campaña `auto_send`) |
| `NEXUS_GMAIL_POLL_INTERVAL_SEC` | Frecuencia poll Gmail |
| `NEXUS_INBOUND_AUTO_REPLY=1` | Respuesta automática tras inbound |

Estado: `GET /health/automation` y **Centro de Operaciones** en la UI (manager/gerente).

## Endpoints de salud

| Ruta | Uso |
|------|-----|
| `GET /health` | Liveness, flags `real_mode` / testing |
| `GET /health/openai` | Clave y modelo configurados |
| `GET /health/whatsapp` | Token Meta o dry run |
| `GET /health/automation` | Scheduler y últimos jobs |

## Seguridad

- No commitear `backend/.env` ni `frontend/.env` (están en `.gitignore`).
- Rotar `NEXUS_JWT_SECRET` y tokens OAuth si hubo filtración.
- `GOOGLE_OAUTH_STATE_SECRET` recomendado en prod (o usa `GOOGLE_CLIENT_SECRET`).
- Deshabilitar endpoints de dev: no definir `APP_ENV=development` ni `TEST_MODE=true` en prod.
- Limitar acceso al API con firewall / VPN si es demo interna.

## Base de datos

Hoy: **SQLite** en `backend/data/nexus_sales.db`. Para producción seria conviene Postgres + backups; el código aún asume SQLite en `session.py`.

Reset local (solo dev): borrar el `.db` y reiniciar uvicorn.

## Orden sugerido de go-live (MVP sin WhatsApp)

**Canales del MVP:** Gmail + Calendar + LinkedIn asistido.  
**WhatsApp:** pendiente (Meta / plantillas). Los días 7 y 16 se **omiten** si WA no está configurado o no está en los canales de la campaña.

1. Backend + health OK, `NEXUS_REAL_MODE=1`, `NEXUS_SKIP_DEMO_SEED=1`.
2. Frontend build con `VITE_API_URL` correcto.
3. Login con usuarios reales (signup `/registro` con `NEXUS_ALLOW_WORKSPACE_SIGNUP=1`, o admin `/companies` + `/users`).
4. Google OAuth por SDR (Integraciones → Gmail + Calendar).
5. Extensión LinkedIn + prueba Día 4.
6. OpenAI + secuencia email Día 1 → respuesta → reunión Calendar.
7. Activar scheduler y auto-send de a uno, monitoreando Operaciones.
8. **Después:** WhatsApp Meta real (ver `WHATSAPP_SETUP.md`) — único canal pendiente para el pitch multicanal completo.

## CI

GitHub Actions (`.github/workflows/ci.yml`): pytest en backend + build del frontend en cada push/PR a `main`/`master`.

Local:

```bash
cd backend && pip install -r requirements.txt -r requirements-dev.txt && python -m pytest -q
cd frontend && npm ci && npm run build
```

## Soporte / diagnóstico rápido

| Síntoma | Revisar |
|---------|---------|
| CORS en browser | `NEXUS_CORS_ORIGINS`, `NEXUS_FRONTEND_URL` |
| OAuth Google vuelve con error | `GOOGLE_REDIRECT_URI`, `NEXUS_FRONTEND_URL`, logs uvicorn |
| WhatsApp 401 / 131030 | Token, lista de prueba Meta, formato teléfono AR — `WHATSAPP_SETUP.md` |
| Secuencia no avanza | `NEXUS_REAL_MODE`, pausa de secuencia, Centro de Operaciones |
| OpenAI rate limit | `GET /health/openai`, logs; en dev existe fallback opcional |
