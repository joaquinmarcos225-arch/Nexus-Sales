# Checklist deploy — Nexus Sales (producto)

Canales de producción: **Gmail + Calendar + LinkedIn asistido + WhatsApp Web asistido** (extensión Chrome).

## 0. Levantar stack

### Opción A — Docker (recomendada para staging / cliente)

```bash
cp .env.production.example .env.production
# Completar NEXUS_JWT_SECRET, NEXUS_TOKEN_FERNET_KEY, OPENAI_*, GOOGLE_*

python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose --env-file .env.production up -d --build
```

- App: http://localhost:8080
- API health: http://localhost:8002/health — debe mostrar `prod_ready: true` en prod

### Opción B — Local (dev)

Ver `README.md`. Flags mínimos en `backend/.env`:

```env
NEXUS_REAL_MODE=1
NEXUS_SKIP_DEMO_SEED=1
NEXUS_ALLOW_WORKSPACE_SIGNUP=1
NEXUS_JWT_SECRET=<string-largo-aleatorio>
```

## 1. Alta del cliente (~10 min)

- [ ] Abrir `/registro` y crear empresa + usuario gerente
- [ ] Login con ese usuario
- [ ] Crear producto (si el alta no dejó uno usable)
- [ ] Crear campaña (canales: LinkedIn → Email → WhatsApp)
- [ ] Crear 1–2 usuarios SDR (Equipo) o usar el gerente para la prueba

## 2. Canales del SDR (~15 min)

- [ ] Integraciones → conectar **Google** (Gmail + Calendar)
- [ ] Instalar extensión Nexus Chrome ≥ **0.18.72** (`browser-extension/TEAM_INSTALL.md`)
- [ ] Verificar extensión activa en Integraciones (LinkedIn + WhatsApp Web)
- [ ] Abrir WhatsApp Web en el mismo Chrome del SDR

## 3. Flujo email → reunión (crítico)

- [ ] Prospecto con email real
- [ ] Iniciar secuencia
- [ ] Ejecutar **Día 1** (email Gmail)
- [ ] Responder desde el mail del prospecto pidiendo reunión
- [ ] Sincronizar Gmail / esperar scheduler
- [ ] Verificar: inbound + reunión en Calendar + panel Reuniones

## 4. Flujo LinkedIn asistido

- [ ] Prospecto con LinkedIn `/in/...` real
- [ ] Cola LinkedIn → Contactar → Copiar y Enviar → marcar enviado

## 5. Flujo WhatsApp Web asistido

- [ ] Prospecto con **teléfono completo** (no enmascarado; enrichment Prospeo con enrich-person)
- [ ] Cola WhatsApp → abrir chat → enviar manual → marcar enviado
- [ ] Registrar inbound WA (extensión) → borrador en Responder

## 6. Go-live

| Criterio | OK? |
|----------|-----|
| `/health` prod_ready | |
| Login + alta workspace | |
| Gmail send + inbound + Calendar | |
| LinkedIn asistido end-to-end | |
| WhatsApp Web asistido end-to-end | |
| Sin seed demo en la BD | |

Tiempo objetivo: **≤ 90 min** desde cero hasta los 3 flujos E2E verdes.

## Referencias

- Deploy: `docs/PRODUCTION.md`
- Setup vendedora: `docs/VENDEDORA_SETUP.md`
- Extensión: `browser-extension/TEAM_INSTALL.md`
- Billing ops: `docs/BILLING.md`
