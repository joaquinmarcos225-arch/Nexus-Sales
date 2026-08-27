# Extensión Nexus — instalación con un clic

## Objetivo

El SDR no descarga ZIP ni usa `chrome://extensions`. Nexus muestra **Agregar a Chrome** y la
instalación/actualización queda a cargo de Chrome Web Store.

Chrome no permite que una web instale una extensión silenciosamente. Para usuarios externos, la
vía soportada es publicar la extensión en Chrome Web Store (puede ser **unlisted**, accesible solo
con el enlace).

## Bloqueo de publicación (importante)

**No subir** el árbol completo `browser-extension/` ni un ZIP generado sin
`NEXUS_EXTENSION_STORE_BUILD=1`. Ese código incluye LinkedIn (cookies, Voyager, probes, inbox DOM)
aunque esté detrás de flags LI-SAFE. Chrome revisa todo el código empaquetado.

El paquete publicable sale **solo** de `browser-extension-store/` mediante allowlist en
`scripts/pack-extension.mjs`: Nexus bridge + WhatsApp asistido (abrir chat, pegar, mark-sent
tras gesto humano). Sin hosts ni archivos LinkedIn.

LinkedIn en producto sigue LI-SAFE desde Nexus (`window.open` + copy + mark-sent / Respondieron),
sin scripts de LinkedIn en la extensión de Store.

## Preparar el ZIP de publicación

Desde la raíz:

```powershell
$env:NEXUS_EXTENSION_STORE_BUILD="1"
$env:NEXUS_FRONTEND_URL="https://nexus.costguard.com.ar"
$env:NEXUS_API_PUBLIC_URL="https://api-production-21aa.up.railway.app"
node scripts/pack-extension.mjs
```

Salida:

`dist/nexus-outreach-assist-store.zip` (v0.19.0+)

El empaquetador:

- Copia únicamente la allowlist de `browser-extension-store/`.
- Falla si encuentra `linkedin`, Voyager, cookies, probes, etc.
- No incluye localhost, docs internas ni tests.
- Descarga en producto (`/extension/...` vía backend) sigue usando el árbol completo para
  instalación manual temporal; no es el ZIP de Store.

## Únicos pasos externos

1. Dar de alta la cuenta de desarrollador en Chrome Web Store (pago único de Google).
2. Crear el item y subir **solo** `dist/nexus-outreach-assist-store.zip`.
3. Completar ficha, justificación de permisos y política de privacidad
   (`https://nexus.costguard.com.ar/privacidad-extension`).
4. Publicar como **Unlisted** inicialmente.
5. Copiar la URL final `https://chromewebstore.google.com/detail/...`.

## Texto para pegar en la ficha (Unlisted)

**Nombre:** Nexus Sales — Outreach Assist

**Resumen (132 caracteres máx):**
Asiste outreach iniciado en Nexus Sales: abre WhatsApp Web, pega el mensaje preparado y confirma el envío con un clic humano.

**Descripción detallada:**
Nexus Sales — Outreach Assist is the Chrome companion for CostGuard's Nexus Sales product.

A signed-in seller starts a task in Nexus. The extension opens WhatsApp Web, fills the prepared message, and waits for the seller to press Enter or click Send. It never sends on its own.

Use it only with a Nexus Sales account (https://nexus.costguard.com.ar). Updates are delivered by Chrome Web Store.

**Categoría:** Productivity / Workflow & Planning  
**Idioma:** Español (también podés marcar English)  
**Visibilidad:** Unlisted  
**Sitio web:** https://nexus.costguard.com.ar  
**Política de privacidad:** https://nexus.costguard.com.ar/privacidad-extension  
**Soporte:** joaquin@costguard.com.ar

### Justificación de permisos

- **storage** — Guardar identificador de empresa y sesión de Nexus en este Chrome.
- **tabs** — Abrir o enfocar la pestaña de WhatsApp Web cuando el vendedor inicia la tarea en Nexus.
- **scripting** — Insertar el mensaje ya generado en el compositor de WhatsApp Web; no pulsa Enviar.
- **https://web.whatsapp.com/** — Operar el chat que el vendedor ya tiene abierto.
- **https://nexus.costguard.com.ar/** y API — Detectar la extensión y devolver el resultado (enviado / no enviado) a Nexus.

**Single purpose (inglés, para la revisión):** Assist user-initiated WhatsApp Web outreach from the Nexus Sales app. No background scraping. No automatic send.

## Activar el botón en Nexus

Configurar en Vercel (Production):

```text
VITE_NEXUS_LINKEDIN_EXTENSION_URL=https://chromewebstore.google.com/detail/...
```

Redeploy de `frontend`. Desde ese momento Configuración → Integraciones muestra
**Agregar a Chrome**; el ZIP manual queda oculto.

## Revisión antes de enviar

- Versión del manifest incrementada.
- Solo permisos: `storage`, `tabs`, `scripting` + hosts WhatsApp / Nexus / API.
- Sin `cookies`, sin `linkedin.com`, sin código LinkedIn en el ZIP.
- WhatsApp: abre un chat, rellena composer; **nunca** pulsa Enviar; mark-sent solo tras Enter/click humano.
- Política de privacidad pública disponible.
- Descripción de propósito único: outreach asistido iniciado desde Nexus en WhatsApp Web.
