# Setup vendedora — Nexus Sales

Checklist operativo para que una SDR use Nexus a diario sin soporte técnico.

Kickoff en call (10 min): `docs/KICKOFF_CLIENTE.md`. Si algo falla: app → **Soporte**.

## 1. Cuenta y acceso (~5 min)

- [ ] Alta Ops post-pago (empresa + owner + SDR):  
  `python backend/scripts/setup_cliente.py --company-name "..." --plan growth --owner-email ... --owner-password ... --sdr-email ... --sdr-password ... --sdr-credits 500`
- [ ] O solo SDR en empresa existente:  
  `python backend/scripts/setup_vendedora.py --company-id N --email ... --credits 50`
- [ ] Login con nombre visible (firma en mensajes)
- [ ] Créditos asignados (mínimo 30 para primera campaña)
- [ ] Consola → **Go-live** (checks de empresa + integraciones)

## 2. Integraciones (~15 min)

- [ ] **Google** conectado (Gmail + Calendar) → Configuración → Integraciones
- [ ] Extensión Nexus Chrome **≥ 0.18.72** instalada (`browser-extension/TEAM_INSTALL.md`)
- [ ] WhatsApp Web abierto en el mismo Chrome del SDR

Verificar en Integraciones: Gmail, Calendar y extensión en verde.

## 3. Campaña recomendada (~10 min)

- [ ] **Nueva campaña** con producto Nexus (o el que vende)
- [ ] Plantilla **LinkedIn → Email → WhatsApp** (3 toques)
- [ ] Canales: LinkedIn + Email + WhatsApp activos
- [ ] ICP mínimo: rol + industria + país (B2B)
- [ ] 20–50 prospecciones (créditos al iniciar secuencia)

## 4. Prospectos

- [ ] Insert manual o import CSV con LinkedIn `/in/...` real
- [ ] O dejar que Nexus busque al **Iniciar secuencia**
- [ ] Teléfonos: deben enriquecerse completos (no enmascarados) para WA

## 5. Rutina diaria SDR

| Momento | Acción |
|---------|--------|
| Mañana | Consola → **Requiere tu acción** → LinkedIn / WhatsApp |
| Mediodía | Consola → tab **Responder** (inbound email + LI + WA) |
| Tarde | **Reuniones** + marcar enviados en colas |

Flujo LinkedIn: **Contactar** → **Enviar mensaje** → tilde manual enviado.  
Flujo WA: cola → abrir WhatsApp Web → enviar → marcar enviado.  
Inbound LI: **Respondieron** → pegar mensaje → borrador en Responder.

## 6. Go-live (5 días sin soporte)

| Día | Criterio |
|-----|----------|
| 1 | Cola LI procesada sin ayuda |
| 2 | Al menos 1 inbound registrado en Responder |
| 3 | Email automático + reunión o follow-up |
| 4 | WA encolado con teléfono real |
| 5 | Cero intervención ops en secuencia/créditos |

## Referencias

- Deploy cliente: `docs/DEPLOY_CHECKLIST.md`
- Extensión: `browser-extension/TEAM_INSTALL.md`
