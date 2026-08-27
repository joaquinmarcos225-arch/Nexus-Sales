# Nexus Sales — Guion de demo y checklist

Demo local: backend `http://127.0.0.1:8002` · frontend `http://127.0.0.1:5173` · extensión Chrome **v0.6.0+**.

## Usuarios demo

| Rol | Email | Contraseña |
|-----|-------|------------|
| Directora | `director@test.com` | `demo123` |
| Manager | `manager@test.com` | `demo123` |
| SDR | `sdr@test.com` | `demo123` |

Campaña de prueba: **Outbound LATAM Q1.2** (id 4) · prospecto **Mia Álvarez** (id 10).

---

## Guion (~25 min)

### 1. Créditos (5 min) — `director@test.com`

1. Ir a **Créditos de contacto**.
2. Mostrar plan Starter (4.000/ciclo) y pool sin asignar.
3. Asignar créditos a un **Manager**.
4. (Opcional) Historial de movimientos al final de la página.

### 2. Manager reparte (2 min) — `manager@test.com`

1. **Créditos** → transferir al SDR o a sí mismo.
2. Ver saldo disponible en el header.

### 3. Campaña + prospecciones (5 min) — `sdr@test.com`

1. **Campañas** → abrir campaña 4 o crear una nueva.
2. Elegir **prospecciones** (= créditos reservados al crear).
3. Importar / sourcing de contactos.
4. Verificar que los créditos disponibles bajaron.

### 4. Secuencia outbound (5 min)

1. **Prospectos** → Mia → **Ver secuencia**.
2. **Iniciar secuencia** → **Ejecutar toque** Día 1 (email) o Día 4 (LinkedIn).
3. Mensaje debe decir **nombre del SDR** y **CostGuard Demo Client** (no el nombre de la campaña).

### 5. LinkedIn asistido (clave — 5 min)

> **Regla de producto:** Nexus **nunca** envía en LinkedIn solo. El SDR pega y aprieta Enviar. La extensión **detecta** el envío y marca en Nexus.

1. Cola LinkedIn → **Abrir LinkedIn** (extensión pega el mensaje).
2. En LinkedIn: revisar y **Enviar manualmente**.
3. Toast extensión: «mensaje marcado como enviado».
4. Cola Nexus baja sola (sin botón «Marcar enviado»).

### 6. Inbound LinkedIn (5 min)

1. Mia responde en LinkedIn real (o simular con extensión en `linkedin.com/messaging`).
2. Nexus registra inbound → réplica en cola (delay ~2 min si está configurado).
3. **Abrir LinkedIn** → pegar réplica → **Enviar manualmente**.
4. Extensión marca enviado automáticamente.

### 7. Follow-up post-secuencia (3 min)

1. Completar los 7 toques o usar script: `python scripts/setup_test_mia_post_sequence_followup.py`
2. Campaña → **Ejecutar follow-ups programados**.
3. Mensaje corto con contexto ICP (no repetir pitch largo).

### 8. WhatsApp — toques Día 7 y 16 (opcional, ~5 min)

> Con `WHATSAPP_DRY_RUN=1` podés mostrar el flujo sin Meta en vivo. Mañana con tokens reales: mismo flujo, envío real.

1. **Configuración → Integraciones** → verificar WhatsApp (simulado o conectado).
2. En campaña activa, barra **Cupo de prospección** muestra importados vs meta ICP.
3. Script de prueba: `python scripts/setup_test_whatsapp_d7.py` (prepara prospecto en Día 7).
4. Mostrar toque WhatsApp en cola / historial de secuencia del prospecto.

---

## Checklist pre-demo

### Infra

- [ ] Backend corriendo en `8002` (reiniciar tras cambios de API)
- [ ] Frontend en `5173`
- [ ] `backend/.env` con `OPENAI_API_KEY` si querés copy IA (sin key: fallback offline)
- [ ] Extensión cargada en Chrome (`chrome://extensions` → recargar)
- [ ] Logueado en Nexus con la extensión instalada (sync auth cada 15 s)

### LinkedIn (crítico)

- [ ] **No** hay auto-envío: solo asistencia + detección post-envío
- [ ] Perfil Mia con URL real `linkedin.com/in/...`
- [ ] Pestaña LinkedIn messaging abierta o perfil del prospecto
- [ ] Cuenta LinkedIn del SDR conectada con Mia (para chat real)

### Datos demo

- [ ] Pool créditos: 4.000 sin asignar (o re-seed)
- [ ] Campaña 4 con producto **Plataforma Nexus** y remitente **Joaquin**
- [ ] Mia en estado **contactado** para follow-ups

### Gmail (si probás email real)

- [ ] Integración Gmail conectada en **Configuración → Integraciones**
- [ ] `ENABLE_GMAIL_AUTOMATION` según lo que quieras mostrar (borradores vs auto)

---

## Checklist post-demo / venta

- [ ] ¿Copy del Día 1 convence? Anotar frases malas
- [ ] ¿ICP del cliente real encaja con plantilla?
- [ ] ¿Créditos del plan correcto (Starter/Growth/…)?
- [ ] Alta cliente nuevo vía **Registrar empresa** (`/registro`, requiere `NEXUS_ALLOW_WORKSPACE_SIGNUP=1` en backend)

---

## Scripts útiles

```powershell
cd backend
python scripts/setup_test_mia_linkedin.py
python scripts/setup_test_mia_post_sequence_followup.py
python scripts/setup_test_whatsapp_d7.py
python scripts/verify_linkedin_flows.py
python scripts/verify_ops_sourcing.py
```

---

## Qué NO prometer en demo

- Envío automático de LinkedIn (riesgo de ban)
- WhatsApp / email auto sin revisión humana (según modo campaña)
- Cobro en dinero (el producto usa **créditos de contacto**, no facturación integrada aún)
