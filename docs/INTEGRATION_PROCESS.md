# Proceso de integración de Nexus en su empresa

## Fase 1 — Antes de tocar Nexus (cliente)

Ellos completan un formulario con:

- Nombre de la empresa
- Cuántos SDRs, managers y directores
- Email de cada persona (o al menos del contacto principal)
- CRM que usan (HubSpot / Salesforce)
- Canales que van a usar (Gmail, Calendar, LinkedIn, WhatsApp)

## Fase 2 — Setup Nexus (ustedes, ops / CostGuard)

- Crear la empresa en Nexus
- Crear usuarios con rol correcto (SDR / manager / gerente)
- Conectar CRM en el servidor (tokens OAuth o `.env`) — el cliente no lo ve
- Configurar producto, campaña base, secuencia, créditos del plan
- **LinkedIn (extensión):** habilitar *Nexus LinkedIn Assist* en los Chrome del equipo (parte de la integración; el SDR no la “arma” solo)
- **WhatsApp Cloud API:** verificación Meta / WABA, número y tokens en el servidor (nivel empresa; no lo conecta cada SDR)
- **Google OAuth (servidor):** `GOOGLE_CLIENT_ID` / secret / redirect una vez — habilita que cada usuario conecte Gmail y Calendar
- Enviar mail: “Tu workspace está listo” + credenciales o link de login

### Quién conecta qué

| Canal | Quién lo deja listo | Qué hace el usuario |
|--------|---------------------|---------------------|
| LinkedIn | CostGuard / admin (extensión) | URL del perfil + sesión en Chrome |
| Gmail | OAuth del servidor (ops) | Cada SDR: «Conectar Google» con su cuenta |
| Calendar | Idem Gmail | Idem (mismo OAuth) |
| WhatsApp | CostGuard / ops (Meta + servidor) | Solo usa el canal en secuencias |
| CRM | CostGuard / ops | Nada — sync invisible |

## Fase 3 — Cada usuario entra (cliente)

- Login con email + contraseña + su nombre
- Configuración → Mis canales:
  - **Gmail / Calendar:** conectar con su cuenta Google
  - **LinkedIn:** pegar URL del perfil + confirmar sesión (la extensión ya la dejó el equipo)
  - **WhatsApp:** no hay “conectar mi WhatsApp”; si el canal está habilitado a nivel empresa, ya está
- Empezar a trabajar en Consola / Campañas

## Fase 4 — Sync automático (invisible)

- Toques enviados → contacto + nota/tarea en CRM
- Respuestas inbound → actividad en CRM
- Reuniones agendadas → actividad en CRM

El cliente **nunca** configura CRM. Ustedes lo hacen en la integración; ellos solo conectan sus canales personales (Gmail/Calendar y perfil LinkedIn).
