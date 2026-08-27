# Instalación de la extensión Nexus — LinkedIn Assist



Para todo el equipo SDR (una vez por navegador).



## 1. Obtener el paquete



**Opción A — ZIP (recomendado para el equipo)**



El admin genera el paquete desde el repo:



```bash

# Solo desarrollo (localhost)

node scripts/pack-extension.mjs



# Producción (incluye tu URL de Nexus)

NEXUS_FRONTEND_URL=https://app.tuempresa.com node scripts/pack-extension.mjs

```



Salida: `dist/nexus-linkedin-assist.zip`



Descomprimí el ZIP y usá la carpeta resultante en el paso 2.



**Opción B — Carpeta del repo**



- Carpeta `browser-extension/` del proyecto (solo si desarrollás en local con `localhost:5173`)



## 2. Instalar en Chrome



1. Abrí `chrome://extensions`

2. Activá **Modo desarrollador**

3. **Cargar descomprimida** → elegí la carpeta descomprimida (debe contener `manifest.json`)

4. Verificá versión **0.3.0** o superior y el ícono **N** de Nexus



## 3. Producción (dominio propio)



Si Nexus no está en `localhost`, el ZIP **debe** generarse con `NEXUS_FRONTEND_URL`:



```bash

NEXUS_FRONTEND_URL=https://app.tuempresa.com node scripts/pack-extension.mjs

```



Eso inyecta tu dominio en el `manifest.json` del paquete. Sin esto, la extensión no detecta Nexus en producción.



Alternativa solo para dev del manifest en el repo (sin ZIP):



```bash

NEXUS_FRONTEND_URL=https://app.tuempresa.com node scripts/build-extension-manifest.mjs

```



Luego recargá la extensión en Chrome.



## 4. Verificar en Nexus



1. Iniciá sesión en Nexus

2. **Configuración → Integraciones → LinkedIn → Verificar extensión**

3. Debe decir **Extensión detectada**



## 5. Uso diario



**Contactar** → Cola LinkedIn → **Enviar mensaje** → revisar en LinkedIn → **Marcar como enviado**.

**Inbound:** con Nexus abierto y logueado, la extensión detecta respuestas en LinkedIn Messaging y las registra en la cola (badge **Responder**).



---



**WhatsApp** no usa extensión: envío automático por API (Día 7 y Día 16). Ver **Configuración → Integraciones → WhatsApp**.



## Admin — checklist rápido



| Paso | Acción |

|------|--------|

| 1 | `NEXUS_FRONTEND_URL=... node scripts/pack-extension.mjs` |

| 2 | Enviar `dist/nexus-linkedin-assist.zip` al equipo |

| 3 | Cada SDR instala y verifica en Integraciones |

| 4 | Probar un envío desde Cola LinkedIn |


