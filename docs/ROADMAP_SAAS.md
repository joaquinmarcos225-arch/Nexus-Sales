# Roadmap SaaS (~10 empresas, sales-led)

Lista actualizada 2026-08-14. Reunión → pago → alta Ops. Sin registro público self-serve.

| # | Qué | Quién | Estado |
|---|-----|--------|--------|
| **1** | **Deploy** — HTTPS (Vercel Sales+Support + Railway API) | Hecho | **Hecho** — nexus / support / api |
| **2** | **Cobro** — factura + Ops (sin checkout tipo ChatGPT) | Lo definís vos | Pendiente definición |
| **3** | **Kickoff 10 min** — Google, extensión, 1ª campaña | `docs/KICKOFF_CLIENTE.md` | Hecho |
| **4** | **Nexus Support** — app aparte; en Sales solo **Soporte** | Deploy en support.costguard.com.ar | Hecho |
| **5** | **CRM** — sync automático (sin UI en Configuración) | Ops/server | Hecho (UI sacada; OAuth por cliente si hace falta) |
| **6** | **Validar** WA + timeout 3 días en 1 campaña real | Juntos en prod | Pendiente |

## Hecho esta semana (no rehacer)

Deploy cloud · dedup prospectos entre vendedores de la misma empresa · extensión en Configuración · olvidé contraseña · CRM fuera de la UI · timeout 3d LI/WA · WhatsApp usable en sourcing · alta Ops · Go-live limpio · Soporte + PWA · observabilidad interna en Support (providers, costos, límites, jobs y errores).

## Siguiente (orden)

| # | Qué | Notas |
|---|-----|--------|
| **A** | Google OAuth público (no testers) | In production + `/inicio` y `/privacidad` online. Falta: branding con homepage `/inicio` + video YouTube + submit scopes (`docs/GOOGLE_OAUTH_VERIFICATION.md`) |
| **B** | Extensión Chrome instalación 1 clic | Hecho — Store unlisted + botón Integraciones |
| **C** | SMTP “olvidé contraseña” | `NEXUS_SMTP_*` en Railway para que el código llegue al mail |
| **D** | Cuentas de vendedores | Cuando pases emails (y contraseñas temporales o que elijan vía reset) |
| **E** | Validar campaña real | Email + LI + WA + timeout 3d |
| **F** | Cobro | Definís modelo; yo lo dejo operable en Ops |
| **G** | HubSpot CostGuard ↔ Nexus | Conectar el HubSpot **nuestro** (CostGuard como empresa que usa HubSpot + Nexus); sync automático server-side |

## Deuda observabilidad Support (guardar — 2026-08-14)

v1 del panel **Operaciones** está hecha y en prod. Pendiente / a medias:

1. **Costos = estimados**, no consumo real de OpenAI / Prospeo / Brave.
2. **No hay snapshot por empresa** (`/support/ops/companies/{id}/…`) — solo vista global.
3. **`/billing-ops/board`** sigue sin gate estricto de Support ops (queda de antes).
4. **Prospeo “sin lectura reciente”** hasta pulsar actualizar saldo.
5. **Dev local** Vite a veces cae o salta a 5175 si 5174 está ocupado; prod es lo estable.
6. **Fuera de este trabajo (lista grande):** SMTP · HubSpot CostGuard · campaña real · calidad ICP.
