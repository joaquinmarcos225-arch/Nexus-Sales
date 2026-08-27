# WhatsApp en Nexus — setup y go-live

Nexus envía WhatsApp **automático** en **Día 7** y **Día 16** de la secuencia (como Gmail), vía **WhatsApp Business Cloud API** (Meta Graph).

---

## Fase 1 — Probar Nexus sin Meta (ahora)

Mientras Meta no confirme la cuenta, usá **dry run**: todo el flujo en Nexus funciona; no se llama a Meta ni llega SMS al celular.

### `backend/.env`

```env
NEXUS_REAL_MODE=1
NEXUS_ENABLE_SEQUENCE_TESTING=1
WHATSAPP_DRY_RUN=1
```

### Reiniciar backend

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

### Comprobar

| Dónde | Esperado |
|-------|----------|
| [http://127.0.0.1:8002/health/whatsapp](http://127.0.0.1:8002/health/whatsapp) | `"dry_run": true` |
| Nexus → Integraciones → WhatsApp → Verificar API | Modo prueba activo |

### Prospecto de prueba

```powershell
cd backend
python scripts/setup_test_whatsapp_d7.py
```

Luego en UI: **Prospectos → Test WhatsApp D7** → si falta borrador, **Generar secuencia** → **Ejecutar toque Día 7**.

**OK:** Día 7 enviado, historial `[WhatsApp · secuencia Día 7]`, aviso de dry run.

---

## Fase 2 — Conectar Meta (cuando la cuenta esté confirmada)

### Variables reales

```env
# Quitar o poner en 0:
# WHATSAPP_DRY_RUN=0

WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_BUSINESS_ACCOUNT_ID=...   # opcional
WHATSAPP_API_VERSION=v21.0
```

### Go-live checklist

1. Integraciones → WhatsApp → **Verificar API** (deep) → línea visible, sin dry run.
2. En Meta → API Setup → agregar **números de prueba** (dev) o **plantillas aprobadas** (prod cold).
3. Prospecto con `phone` o `whatsapp` → ejecutar Día 7 → mensaje en el celular.
4. Revisar `whatsapp_message_id` en historial del prospecto.

Documentación oficial Meta: [WhatsApp Cloud API — Get Started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started/)

---

## Si Meta pide confirmar cuenta y el SMS no llega

Solo métodos **oficiales y seguros** (no uses números virtuales random; Meta los bloquea y es riesgo de cuenta).

### Opción 1 — Tarjeta de crédito/débito (recomendada)

Meta permite verificar identidad con método de pago en lugar del SMS.

1. En la pantalla **Confirmar cuenta**, buscá **otra forma de verificación** / **agregar tarjeta**.
2. Guía oficial: [Confirmar identidad con tarjeta](https://www.facebook.com/help/242462812536016)
3. Podés usar tarjeta de **CostGuard** (no hay cargo por verificar; es identidad).
4. Volvé a **Business Manager → Crear app**.

### Opción 2 — Verificar el celular en Facebook primero

A veces Developers funciona si el número ya está verificado en la cuenta de Facebook.

1. [Centro de cuentas Meta — Información personal](https://accountscenter.facebook.com/personal_info)
2. Agregá el número en formato internacional: `+54911...` (sin el 15 local).
3. Completá el código **desde la app de Facebook en el celular** (notificación push a veces funciona cuando el SMS no).
4. Esperá **24 h** sin reintentar SMS en Developers.
5. Reintentá crear la app.

### Opción 3 — Otro admin de CostGuard (más rápido en equipos)

1. Compañero con celular que **sí reciba SMS** entra a [developers.facebook.com](https://developers.facebook.com).
2. Crea la app **Business** en el [Business Manager de CostGuard](https://business.facebook.com/settings).
3. Agrega WhatsApp → copia **Phone number ID** y **token**.
4. Te agrega a vos como **Administrador** de la app: [Roles de la app](https://developers.facebook.com/docs/development/build-and-test/app-roles)
5. Vos pegás credenciales en `backend/.env` — los activos quedan en CostGuard.

### Opción 4 — Soporte oficial Meta

Si probaste tarjeta + otro número + 48 h de espera:

1. [Meta for Developers — Support](https://developers.facebook.com/support/)
2. Tema: **Account registration / Cannot receive SMS verification code**
3. País: Argentina, Business: CostGuard, describí que el SMS no llega a varios números.

### Opción 5 — App Meta Business Suite en el celular

1. Instalá [Meta Business Suite](https://www.facebook.com/business/tools/meta-business-suite) en un celular con señal.
2. Iniciá sesión con la cuenta que administra CostGuard.
3. Completá verificaciones de seguridad desde la app.
4. Reintentá crear la app desde [business.facebook.com](https://business.facebook.com).

---

## Enlaces oficiales útiles

| Recurso | URL |
|---------|-----|
| Business Manager | https://business.facebook.com |
| Developers | https://developers.facebook.com |
| WhatsApp Cloud API | https://developers.facebook.com/docs/whatsapp/cloud-api/ |
| API Setup (token / phone id) | Desde tu app → WhatsApp → API Setup |
| System User + token permanente | https://developers.facebook.com/docs/whatsapp/business-management-api/get-started#system-user-access-tokens |
| Plantillas de mensaje | https://business.facebook.com/wa/manage/message-templates/ |

---

## Cold outreach en producción

Texto libre solo dentro de **ventana 24 h** tras mensaje del prospecto. Para contacto en frío masivo necesitás **plantillas aprobadas** en WhatsApp Manager. Si Graph API rechaza el envío, Nexus deja el toque en **fallido** con el error de Meta.

---

## Tests automáticos

```powershell
cd backend
python -m pytest tests/test_whatsapp_dry_run.py tests/test_sequence_touch_whatsapp.py -q
```
