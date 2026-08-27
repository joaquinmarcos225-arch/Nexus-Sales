# HubSpot y Salesforce en Nexus

Nexus ejecuta outreach y secuencias; el CRM almacena contactos, actividades y reporting comercial.

**Ambos CRM pueden estar activos a la vez** — cada evento elegible se replica en HubSpot y Salesforce si la empresa los tiene conectados.

## Modelo

| Sistema | Rol |
|---------|-----|
| **Nexus** | Secuencias, generación IA, Gmail, LinkedIn, WhatsApp |
| **HubSpot / Salesforce** | Contactos, timeline/tareas, reporting comercial |

### Quién configura qué

| Área | Quién | Dónde |
|------|-------|-------|
| Gmail, Calendar, WhatsApp, LinkedIn | Cada usuario (SDR, manager, director) | **Configuración → Mis canales** en la app |
| HubSpot / Salesforce | **Equipo Nexus** (durante la integración del cliente) | Backend / ops — **no hay pantalla en la app para el cliente** |

### Alta de clientes (proceso Nexus)

Al integrar un cliente nuevo:

1. **Levantar el equipo:** cuántos SDRs, managers y directores necesitan.
2. **Crear usuarios** en Nexus con el rol correspondiente (Equipo o seed interno).
3. **Conectar CRM** en el servidor (tokens OAuth o `.env`) para esa empresa.
4. Cada persona conecta **solo sus canales personales** (Gmail, LinkedIn, etc.) desde Mis canales.

El cliente **no conecta HubSpot ni Salesforce** desde la UI.

## Contrato de sync (mínimo, sin duplicados)

Nexus sincroniza solo estos eventos:

| Evento | Cuándo | Clave anti-duplicado |
|--------|--------|----------------------|
| **Toque enviado** | Email, LinkedIn o WhatsApp confirmado en secuencia | `touch:{día}:{canal}` |
| **Respuesta inbound** | Prospecto responde y hubo outbound previo | `inbound:{canal}:{message_id}` |
| **Reunión agendada** | Meeting confirmado en Nexus | `meeting:{meeting_id}` |

Por cada evento:

- **Upsert de contacto** por email (sin email → no sync).
- **Actividad en CRM**: nota en HubSpot, Task completada en Salesforce.
- Registro idempotente en `crm_sync_events`.

### Exclusión de cuentas ya tocadas (import al conectar)

Al conectar HubSpot o Salesforce (OAuth callback) — y con refresh manual — Nexus importa una **lista de exclusión** desde el CRM:

| Match | Origen típico |
|-------|----------------|
| `email` | Contacto con actividad previa |
| `domain` | Dominio corporativo del email / website de cuenta |
| `company_name` | Nombre de empresa/cuenta tocada |

**Qué cuenta como “tocado”:**
- **HubSpot:** contacto con `notes_last_contacted`, actividad de ventas, notas de contacto > 0, o owner asignado.
- **Salesforce:** Contact/Account con `LastActivityDate`.

Se usa al:
1. Filtrar empresas del sourcing (Web Search).
2. Bloquear alta/import de prospectos (`409` si matchea).

API ops:
```http
GET  /companies/{id}/integrations/crm/exclusions
POST /companies/{id}/integrations/crm/exclusions/sync?provider=hubspot|salesforce
```

**Fuera de alcance:** deals, pipeline bidireccional, campos custom masivos, historial completo de mensajes.

## Configuración del servidor (ops Nexus)

### HubSpot

1. App en HubSpot con scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.notes.write`.
2. OAuth por empresa (recomendado) o token estático legacy.

```env
HUBSPOT_CLIENT_ID=...
HUBSPOT_CLIENT_SECRET=...
HUBSPOT_REDIRECT_URI=http://127.0.0.1:8002/auth/hubspot/callback
CRM_OAUTH_STATE_SECRET=...
NEXUS_FRONTEND_URL=http://127.0.0.1:5173
```

Legacy: `HUBSPOT_ACCESS_TOKEN` + `HUBSPOT_ENABLED=1`.

Flujo OAuth (ops): `GET /auth/hubspot/start-url?company_id=&user_id=` con sesión de gerente Nexus → callback guarda tokens en `company_integrations`.

### Salesforce

```env
SALESFORCE_CLIENT_ID=...
SALESFORCE_CLIENT_SECRET=...
SALESFORCE_REDIRECT_URI=http://127.0.0.1:8002/auth/salesforce/callback
SALESFORCE_LOGIN_URL=https://login.salesforce.com
SALESFORCE_API_VERSION=v59.0
```

Legacy: `SALESFORCE_REFRESH_TOKEN`, `SALESFORCE_INSTANCE_URL`, `SALESFORCE_ENABLED=1`.

Sandbox: `SALESFORCE_LOGIN_URL=https://test.salesforce.com`

## API (interna / ops)

```http
GET  /companies/{company_id}/integrations/hubspot/verify?deep=true
GET  /companies/{company_id}/integrations/salesforce/verify?deep=true
POST /companies/{company_id}/integrations/hubspot/disconnect
POST /companies/{company_id}/integrations/salesforce/disconnect
GET  /companies/{company_id}/crm/sync-status
POST /companies/{company_id}/crm/sync/retry
GET  /companies/{company_id}/integrations/crm/exclusions
POST /companies/{company_id}/integrations/crm/exclusions/sync
GET  /auth/hubspot/start-url?company_id=&user_id=
GET  /auth/salesforce/start-url?company_id=&user_id=
```

Requieren permiso `company.config` (uso interno Nexus al provisionar la empresa).

## Smoke test

Desde `backend/`:

```bash
python scripts/verify_crm_sync_flow.py
python scripts/verify_crm_sync_flow.py --company-id 1
python scripts/verify_crm_sync_flow.py --live
```

## Prospectos sin email

Sin email no hay sync (email es la clave de contacto en ambos CRM).

## Troubleshooting (ops)

| Síntoma | Acción |
|---------|--------|
| OAuth callback falla | Revisar redirect URI en HubSpot/SF y `NEXUS_FRONTEND_URL` |
| Verify 401 | Re-ejecutar OAuth de ops para esa empresa |
| Eventos pendientes | `POST .../crm/sync/retry` o revisar logs `nexus.crm.sync` |
