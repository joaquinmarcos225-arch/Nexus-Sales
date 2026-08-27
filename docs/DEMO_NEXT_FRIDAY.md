# Demo Nexus — viernes próximo (checklist)

Fecha objetivo: próximo viernes (después del 24-jul-2026).

## Orden del show (acordado)

1. Hook
2. Consola
3. Campaña / ICP
4. **Insertar prospecto en vivo** (venta principal)
5. LinkedIn muestra (Christian Damian Mariano)
6. Close / CTA

WhatsApp: `https://wa.me/5491128942875` · Mail: `costguard65@gmail.com` · Landing: `https://costguard.com.ar`

---

## Hecho esta semana

- [x] Mail en modo envío real (`NEXUS_AUTO_SEND_ENABLED=1`)
- [x] Insertar prospecto bajo «Tus campañas» (con/sin campaña, producto, ≥1 canal)
- [x] Director puede tomar prospectos / outreach
- [x] Cupo campaña no bloquea insert manual (auto-amplía / cupo ≥50)
- [x] Gmail operativo en director (cuenta ya autorizada en Nexus)
- [x] Tutorial sin Centro de operaciones
- [x] Dominio `costguard.com.ar` live

## Pendiente crítico (antes del viernes)

### WhatsApp / Meta (ahora)

Guías: `docs/WHATSAPP_TOKEN_RENEW.md` · `docs/WHATSAPP_POST_META_CHECKLIST.md`

- [x] Token Graph Explorer (caduca; regenerar al go-live)
- [x] `WHATSAPP_DRY_RUN=1` — Nexus simula Día 7 mientras Meta está restringido
- [x] Prospecto test Día 7 listo (`prospect_id` 7, script `setup_test_whatsapp_d7.py`)
- [x] Copy plantilla `nexus_outreach_d7` documentado
- [ ] Meta Business Verification: documentos (PDF MP Pacheco de Melo) + aprobación
- [ ] Levantada restricción WABA → número producción + Phone Number ID
- [ ] `WHATSAPP_DRY_RUN=0` + plantilla aprobada + envío real Día 7

### Google OAuth

- [ ] Agregar `costguard65@gmail.com` (y quien use la demo) como **Test user** en Google Cloud Audience *(ops; FROM actual ya conectado alcanza)*
- [ ] Conectar Gmail/Calendar con `costguard65@gmail.com` si el pitch requiere ese remitente
- [x] Banner OAuth success en verde (Integraciones)
- [x] Auto-send de secuencia marca **enviado** (no “borrador”) cuando Gmail API manda
- [x] Día 1 usa Gmail del owner/director si el seller no tiene conexión

### Calidad / LinkedIn

- [x] Copy Día 4 LinkedIn (CostGuard / Nexus, corto para cámara)
- [x] Cola LinkedIn: botón «Marcar como enviado» verde + copy alineado
- [x] Prep LinkedIn Christian Damian Mariano  
  `https://www.linkedin.com/in/christian-damian-mariano-a745859/`  
  Script: `backend/scripts/setup_demo_christian_linkedin.py`  
  (Día 1 email enviado + conexión `connected` + Día 4 en cola mensaje)

### Smoke test día anterior

- [ ] Reiniciar backend con `.env` nuevo
- [ ] Login director/SDR → insertar prospecto → mail llega (toast “enviado”)
- [ ] LinkedIn: `python scripts/setup_demo_christian_linkedin.py` → cola → extensión → marcar enviado
- [ ] WhatsApp Día 7 dry-run OK; real cuando Meta apruebe
- [ ] Script demo 4–5 min ensayado

---

## Notas técnicas rápidas

- Gmail: cuenta ya autorizada en Nexus. Si se necesita FROM costguard65 → OAuth Test user + reconectar.
- LinkedIn demo: path **mensaje** (no Connect). Re-correr el script Christian antes del ensayo.
- Cupo: insert manual amplía cupo +1 crédito si hace falta; campañas puntuales nuevas arrancan en 50.
- Docs WA: `docs/WHATSAPP_SETUP.md` · `docs/WHATSAPP_POST_META_CHECKLIST.md`
