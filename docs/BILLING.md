# Créditos y cobro Ops (B2B)

Nexus Sales es **B2B sales-led**. El cupo de créditos se acredita cuando Ops confirma el pago del mes.

**1 crédito Nexus = 1 persona en secuencia completa (mail + WhatsApp + LinkedIn).**

## Unidad económica (2026-08-26)

| Concepto | USD |
|----------|-----|
| Precio venta / crédito | **0,50** |
| COGS lista / peor caso (móvil siempre) | **~0,30** |
| **COGS efectivo** (pasos 1–4: gate WA + search gratis + lazy móvil + Brave caps) | **~0,18–0,22** |

Prospeo Starter ≈ USD 49 / 2.000 → **USD 0,0245** por crédito Prospeo.  
Móvil = **10** créditos Prospeo + email **1** ≈ **11** si se revela siempre.

### Cómo bajó el COGS efectivo (sin tocar precio ni cupo)

1. Enrich **sin** `enrich_mobile` si la campaña no tiene WhatsApp.
2. Si search ya trae celular usable → no volver a pagar enrich móvil.
3. Móvil diferido al activar / canal WA (no en el batch de sourcing).
4. Menos queries Brave (early-stop, domain fast, 1 query fallback).

Métrica runtime (proceso): `GET /health/cogs-metrics` → costos por módulo.

## Base propia Nexus (v1 — guardar lo pagado)

Tablas globales (no son el tenant):

- `nexus_company_cache` — empresa empleadora (dominio / nombre)
- `nexus_contact_cache` — persona (email / LI slug / teléfono)
- `nexus_contact_deliveries` — qué cliente Nexus ya recibió ese contacto

Se escribe al **importar** y al **channel enrich**.  
Lookup antes de Prospeo: en role-first y B2C (`find_cached_leads_for_campaign`) — no re-entrega al mismo tenant (`nexus_contact_deliveries`).

### Anti-dupe entre clientes (paso C)

| Regla | Comportamiento |
|-------|----------------|
| Mismo tenant | No vuelve a recibir el mismo email/LI/tel (prospects locales + deliveries) |
| Otro tenant | Sí puede recibir el contacto (reuso = ahorro Prospeo) |
| Prospeo | También filtra identidades ya entregadas a ese tenant |
| Import | Bloquea si ya hay delivery aunque venga del pipeline |

Delivery guarda `status` (default `delivered`) y `outcome` opcional (replied/meeting/…).

### Caché de investigación (paso E)

- `nexus_research_cache` — snippets web B2B por empresa/país (TTL 7d) en `ensure_outreach_research`, para no repetir Brave entre prospectos de la misma empresa.
- Dominio corporativo: lectura/escritura en `nexus_company_cache` desde el resolver (hit = sin Brave).

### IA solo donde agrega valor (paso F)

- **Clasificar inbound**: heurísticas primero; OpenAI solo si el mensaje es ambiguo (preguntas largas, señales mixtas).
- **Brief de investigación**: plantilla + snippets por defecto; síntesis OpenAI solo con `NEXUS_RESEARCH_OPENAI_SYNTH=1`.
- Métrica: `openai_skipped_trivial` en `/health/cogs-metrics`.

### Fetch propio antes de Brave (paso G v1)

- `nexus_public_fetch` — GET al sitio corporativo (meta title/description) cuando hay dominio.
- Research outbound B2B: fetch directo → caché snippets → Brave solo si falta señal.
- Resolver dominio: guess + fetch HTTP confirma antes de Prospeo.
- Métrica: `nexus_fetch_calls` en `/health/cogs-metrics`.

### Company-first cache (paso D3)

- **Buscar empresas**: `find_cached_companies_for_campaign` antes de Brave (contactos reutilizables agrupados por dominio).
- **Enrich por empresa**: `find_cached_contacts_for_company` antes de Prospeo search-person.
- Misma anti-dupe por tenant que role-first / B2C.

### Investigación progresiva (paso 5)

- **Import / enrich:** sin Brave ni brief profundo.
- **Primer compose (día 1):** `light` = caché + fetch sitio; **Brave** solo si light vacío y prospecto vale la pena (score ≥72 o campaña WA).
- **Follow-ups (día >1):** reusa brief guardado (`research_skipped` en métricas).
- Override: `NEXUS_RESEARCH_DEPTH=light|full|crm|skip`, `NEXUS_RESEARCH_ESCALATE_BRAVE=0`.

## Planes vigentes (precio fijo · cupo = precio / 0,50)

| Plan | Precio USD | Créditos / mes | $/crédito | COGS lista (0,30) | COGS efectivo (~0,20) | Margen efectivo |
|------|------------|----------------|-----------|-------------------|------------------------|-----------------|
| Starter | **$300** | **600** | $0,50 | $180 | **~$120** | **~$180** |
| Growth | **$500** | **1.000** | $0,50 | $300 | **~$200** | **~$300** |
| Scaler | **$700** | **1.400** | $0,50 | $420 | **~$280** | **~$420** |
| Elite | **$900** | **1.800** | $0,50 | $540 | **~$360** | **~$540** |
| Customized | a medida | a medida | **$0,50** | ~$0,30 | **~$0,20** | **~$0,30** |

Ops sigue presupuestando tools con COGS lista (~0,30) por seguridad; el efectivo se valida con `/health/cogs-metrics`.

### Prospeo detrás de cada plan (triple canal, peor caso ÷12)

| Plan | ≈ créditos Prospeo / mes | ≈ USD Prospeo |
|------|--------------------------|---------------|
| Starter 600 | ~7.200 | ~$176 |
| Growth 1.000 | ~12.000 | ~$294 |
| Scaler 1.400 | ~16.800 | ~$412 |
| Elite 1.800 | ~21.600 | ~$529 |

Con lazy mobile, el gasto Prospeo real suele ser **menor** (solo quien llega a WA o no traía número en search).

## Cupo operativo CostGuard (video / piloto)

Con **un** Prospeo Starter (2.000 créditos) sin packs extra:

```
secuencias_triple ≈ floor(créditos_Prospeo / 12) ≈ 140–150   # peor caso
# con lazy: más secuencias posibles si muchos no pagan los 10 de móvil
```

Eso es techo de tools, no el cupo del plan Nexus del cliente.

## Flujo Ops (`/ops-cobros`)

1. Elegir plan del cliente  
2. Marcar **Sí, pagó**  
3. Cargar montos sugeridos en OpenAI / Prospeo / Brave y marcar cada uno  
4. **Acreditar créditos Nexus** (una vez por `YYYY-MM`)

Sin pago marcado → no hay créditos nuevos.  
El scheduler **no** renueva demos (`billing_status=none`) ni clientes Ops (`billing_provider=ops`). Solo Stripe / MP / dLocal activos.

## UI

- `/creditos` — pool, asignación e historial  
- `/ops-cobros` — Director/Owner: cobro del mes + tools + acreditación  

## API

- `GET /billing-ops/board`  
- `GET /companies/{id}/billing-ops`  
- `POST /companies/{id}/billing-ops/mark-paid`  
- `POST /companies/{id}/billing-ops/tools/{openai|prospeo|brave}`  
- `GET /health/cogs-metrics` — contadores por módulo + USD estimado:
  - Prospeo: `prospeo_search_calls`, `enrich_email_only_calls`, `enrich_mobile_calls`, `prospeo_credits_est`, `est_prospeo_usd`
  - Brave: `brave_queries`, `est_brave_usd`
  - OpenAI: `openai_calls`, tokens, `est_openai_usd`
  - Flujo: `imports`, `wa_sent`, `est_cogs_per_import_usd`, `est_total_usd`
