# Nexus Sales — Extensión LinkedIn Assist



Asiste al SDR **sin enviar automáticamente** (evita bloqueos de LinkedIn):



1. Abre el **perfil** del prospecto

2. Hace clic en **Mensaje**

3. **Pega** el texto de Nexus en el renglón de escribir

4. El humano aprieta **Enviar** en LinkedIn



**v0.6.0** detecta cuando el SDR **envía** (outbound o réplica inbound) y marca en Nexus automáticamente — **nunca** aprieta Enviar por vos.

**v0.5.0** detecta **respuestas inbound** en LinkedIn Messaging y las registra en Nexus (pausa secuencia + borrador de réplica).



## Instalación (Chrome)



1. Abrí `chrome://extensions`

2. Activá **Modo de desarrollador**

3. **Cargar descomprimida** → elegí esta carpeta `browser-extension`

4. En `chrome://extensions`, pulsá **Recargar** en la extensión Nexus (versión **0.3.0+**)

5. Recargá la pestaña de Nexus (`http://127.0.0.1:5173`) con F5



## Uso outbound



1. En Nexus → Centro de outreach → Cola LinkedIn → **Enviar mensaje**

2. Si la extensión está instalada, se abre LinkedIn con el chat y el texto listo

3. Revisá y enviá con Enter

4. En Nexus → **Marcar como enviado (manual)**



## Uso inbound (automático)



1. Mantené sesión iniciada en Nexus (la extensión sincroniza el token cada ~15 s)

2. Abrí **LinkedIn → Mensajes** (`/messaging`)

3. Cuando un prospecto de Nexus responde, la extensión lo registra en la API

4. En Nexus verás la cola actualizada con badge **Responder** y el borrador sugerido



Si el prospecto no se abrió desde Nexus, la extensión intenta resolverlo por URL con `GET /prospects/resolve-linkedin`.



## Sin extensión



Nexus copia el mensaje al portapapeles y abre el perfil. Tenés que abrir Mensaje y pegar vos. Para inbound podés usar **Registrar respuesta LinkedIn** en el panel del prospecto.



## Nota técnica



LinkedIn **no ofrece** un enlace público que abra un chat concreto con el mensaje prefilled ([referencia Stack Overflow](https://stackoverflow.com/questions/15407170/url-scheme-for-linkedin)). Por eso hace falta la extensión en el navegador del SDR.

