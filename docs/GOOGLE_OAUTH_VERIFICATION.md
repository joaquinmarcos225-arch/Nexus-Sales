# Verificación OAuth de Google

Nexus necesita Gmail (enviar, borradores, leer replies) y Calendar (disponibilidad + eventos).
Los clientes de pago no pueden conectar Google hasta **In production + branding verificado + scopes verificados**.

## Checkpoint (2026-08-21) — retomar acá

**Hecho:** Domain Search Console (cuenta correcta) → Branding verificado y **publicado** (“se muestra a los usuarios”).

**Siguiente:** Centro de verificación / Acceso a los datos → scopes Gmail+Calendar → video YouTube unlisted → Prepare for verification.

## Estado (2026-08-20)

Proyecto: **Nexus Sales** (`nexus-sales-505819`), cuenta `joaquin@costguard.com.ar`.

- Audience: **In production** / External (ya no hace falta lista de testers para “entrar” a la app; igual **falta** verificación de scopes restringidos)
- Privacy pública: `https://nexus.costguard.com.ar/privacidad` ✅
- Homepage pública: `https://nexus.costguard.com.ar/inicio` ✅ (el `/` sigue siendo login; **no** usarlo como homepage)
- Data Access: scopes + justificaciones guardados
- Branding verification: reenviar con homepage = `/inicio` (antes rechazado por login en `/`)
- Submit de scopes: falta video YouTube (unlisted) + Prepare for verification

Scopes que pide el backend (`backend/app/services/google_oauth.py`):

| Scope | Tipo Google | Para qué |
|---|---|---|
| `gmail.compose` | Restricted | Borradores y envío |
| `gmail.readonly` | Restricted | Detectar replies, hilos y cuerpos |
| `calendar.events` | Sensitive | Crear/editar/borrar reuniones |
| `calendar.events.freebusy` | Sensitive | Horarios libres |
| `calendar.calendarlist.readonly` | Sensitive | Listar calendarios visibles |
| `userinfo.email` | Non-sensitive | Mostrar qué cuenta quedó conectada |

No usamos `gmail.modify` ni `calendar` completo.

## Siguiente

1. [Branding](https://console.cloud.google.com/auth/branding?project=nexus-sales-505819): Application home page = `https://nexus.costguard.com.ar/inicio` → Save → **Verify branding** / *I have fixed the issues*.
2. Grabar demo 2–4 min, consentimiento en **inglés**, YouTube **unlisted**.
3. [Data Access](https://console.cloud.google.com/auth/scopes?project=nexus-sales-505819): pegar link del video → **Prepare for verification** → Submit.

### Video

1. Login en Nexus Sales.
2. Integraciones → Conectar Google → pantalla de consentimiento completa, scopes visibles.
3. Enviar o crear un borrador Gmail a un prospecto de prueba.
4. Recibir un reply y que Nexus lo muestre / pause la secuencia.
5. Crear una reunión en Calendar desde Nexus.
6. Desconectar Google.

### Justificaciones (ya pegadas en Data Access)

- **gmail.compose:** el vendedor genera un correo en Nexus y lo envía o deja como borrador en su Gmail. Nexus no envía en nombre de Google; usa la cuenta del vendedor.
- **gmail.readonly:** Nexus busca respuestas de los prospectos contactados (`from:` / hilo) para pausar la secuencia, mostrar el inbound y armar el reply.
- **calendar.events:** crear, mover y cancelar la reunión agendada con el prospecto.
- **calendar.events.freebusy:** ofrecer horarios reales sin mostrar el detalle de otros eventos.
- **calendar.calendarlist.readonly:** detectar calendarios visibles para no pisar agenda.
- **userinfo.email:** mostrar en Integraciones qué cuenta Google quedó vinculada.
