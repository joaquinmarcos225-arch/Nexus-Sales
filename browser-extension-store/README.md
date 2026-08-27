# Chrome Web Store package source

Fuente **única** del ZIP publicable (`dist/nexus-outreach-assist-store.zip`).

- Solo WhatsApp asistido + bridge Nexus.
- Sin archivos ni hosts LinkedIn.
- Empaquetar con `NEXUS_EXTENSION_STORE_BUILD=1` vía `scripts/pack-extension.mjs` (allowlist + escaneo negativo).

No uses `browser-extension/` para subir a la Store: ahí sigue el código LinkedIn para sideload temporal.
