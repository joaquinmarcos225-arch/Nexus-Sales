# Nexus Support (app aparte)

No vive dentro de Nexus Sales. Sales solo tiene **Soporte** (chat del cliente). El equipo de Nexus usa esta app.

- Sales (comercial): `frontend/` → `/soporte` — **un hilo por usuario** (no uno por empresa).
- Support (interno): `nexus-support/` → puerto **5174** — bandeja: persona + empresa.

## Dev

```powershell
cd nexus-support
npm install
npm run dev
```

Abrí [http://127.0.0.1:5174](http://127.0.0.1:5174). Login: `/auth/support-login` (no cambia el nombre/firma del SDR).

Quién entra: emails en `NEXUS_SUPPORT_OPS_EMAILS`, o owner/gerente de `NEXUS_OPS_COMPANY_ID` (default 1). En demo: `director@test.com` / `owner@test.com`.

## Celu y notificaciones

Las dos apps son web (PWA). En el teléfono: Chrome/Safari → **Agregar a pantalla de inicio**.

- Sales → Soporte: el mensaje llega acá (Nexus Support).
- Si respondés acá, el cliente lo ve en Sales → Soporte.
- Activá notificaciones en cada app (iPhone: solo si está instalada en inicio, iOS 16.4+).

## Prod

Deploy separado (otro subdominio, ej. `support.tu-dominio.com`). Sumá ese origen a `NEXUS_CORS_ORIGINS`. HTTPS obligatorio para push.
