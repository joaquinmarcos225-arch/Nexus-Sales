# Renovar token WhatsApp (demo viernes)

Bloqueo actual: `WHATSAPP_ACCESS_TOKEN` expiró el **15-jul-2026**. Phone Number ID y WABA ya están bien en `.env`.

## 1) Token desde API Setup (usar esto primero)

CostGuard BM puede bloquear **Usuarios del sistema** con:
*"Esta cuenta comercial no cumplió nuestras Políticas de publicidad u otras normas."*
Eso **no impide** renovar el token por Developers.

1. Abrí [developers.facebook.com](https://developers.facebook.com/apps) → app de WhatsApp / CostGuard
2. **WhatsApp → Configuración de la API** (API Setup)
3. **Generar token de acceso** / Generate access token
4. Autorizá con tu usuario admin y copiá el token
5. Pegalo acá en el chat

Regeneralo el **jueves o viernes a la mañana** de la demo (suelen ser de corta duración).

## 2) Token permanente (System User) — bloqueado por ahora

Cuando Meta levante la restricción del portfolio:

1. [Business Settings → System users](https://business.facebook.com/settings/system-users)
2. Crear System User Admin → Generate token
3. Permisos: `whatsapp_business_messaging` + `whatsapp_business_management`

Revisar/apelar restricción: [Account Quality](https://business.facebook.com/accountquality) → Solicitar revisión.  
**No** crees otro Business Manager para evadir el bloqueo.

## 3) Pegar en Nexus

En el chat con Cursor, pegá el token (o decí “listo, acá va: …”). Se actualiza:

```env
WHATSAPP_ACCESS_TOKEN=<token>
```

en `backend/.env`, se reinicia el backend y se verifica:

`GET http://127.0.0.1:8002/health/whatsapp?deep=true` → `api_reachable: true`

## 4) Número de prueba + Día 7

1. Meta → WhatsApp → API Setup → **Add phone number** (el celular que va a recibir el mensaje en la demo)
2. En backend:

```powershell
cd C:\Users\mjray\OneDrive\Escritorio\Proyecto-J\backend
.\.venv\Scripts\python.exe scripts\setup_test_whatsapp_d7.py
```

3. En la app: prospecto Test WhatsApp D7 → ejecutar Día 7
4. Confirmar que llega al celular y que hay `whatsapp_message_id` en el historial

## IDs ya cargados (no tocar salvo que Meta cambie)

- `WHATSAPP_PHONE_NUMBER_ID=1178898915307914`
- `WHATSAPP_BUSINESS_ACCOUNT_ID=1334710965515017`

## Si “No se pudo generar el token” / System User bloqueado

Causa típica: restricción del portfolio Costguard (políticas Meta).

### Plan A — Graph API Explorer (probar ya)

1. [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. App: **Nexus WPP**
3. **Generate Access Token** → permisos:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
   - `business_management` (si aparece)
4. Copiá el User Token y pegalo en el chat

### Plan B — Calidad de cuenta (arreglar el bloqueo)

1. [Account Quality](https://business.facebook.com/accountquality)
2. Si hay restricción → **Solicitar revisión**
3. No crear otro Business Manager para evadir

### Plan C — Lista de destinatarios (error 131030)

Con el **número de prueba** Meta (`+1 555…`) solo podés escribir a números **verificados por OTP** (máx. ~5).

1. Destinatario → **Agregar número**
2. Formato AR: probar **sin el 9** primero: `+54 3476 362762` (no `+54 9 …`)
3. Meta debe pedir código **SMS o llamada** → ingresarlo
4. Si el curl de Meta muestra un `to` raro (ej. `54347615362762` con un `15` de más), borrá el número y agregalo de nuevo limpio
5. El curl de la derecha **no** es un script para correr a mano: es solo ejemplo

Diagnóstico API: si sigue `131030`, el número **no** quedó en la allowlist (aunque aparezca en el dropdown).

### Plan D — Producción (recomendado para la reunión)

Salir del número de prueba:

1. **Paso 2: Configuración de producción** → registrar **número real** de CostGuard + método de pago
2. Actualizar `WHATSAPP_PHONE_NUMBER_ID` al ID del número real
3. Con número real **no** hay lista de destinatarios de prueba
4. Cold outreach sigue necesitando **plantillas** aprobadas (o ventana 24h)

### Plan E — Demo sin Meta real (último recurso)

```env
WHATSAPP_DRY_RUN=1
```

La UI muestra envío OK; no llega mensaje al celular.
