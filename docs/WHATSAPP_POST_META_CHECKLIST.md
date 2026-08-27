# WhatsApp — checklist post-verificación Meta (demo)

Usar cuando Meta apruebe el negocio y levante la restricción de la WABA.

## Estado Nexus (preparado ya)

- [x] `WHATSAPP_DRY_RUN=1` → Día 7 simulado en Nexus (sin Meta)
- [x] Script: `backend/scripts/setup_test_whatsapp_d7.py`
- [x] Plantilla copy lista (abajo)
- [ ] Token Graph Explorer / System User vigente
- [ ] Número producción registrado + Phone Number ID nuevo en `.env`
- [ ] `WHATSAPP_DRY_RUN=0`
- [ ] Plantilla Meta aprobada + vars en `.env`
- [ ] Envío real Día 7 al celular de prueba

## Cuando Meta diga Verificado

1. WhatsApp Manager → agregar número producción (`+5491128942875` u otro)
2. Método de pago cargado
3. Copiar **Phone Number ID** (y WABA ID si cambió)
4. Regenerar token (Graph Explorer o System User) → pegar en chat / `.env`
5. En `.env`:
   ```env
   WHATSAPP_PHONE_NUMBER_ID=<nuevo>
   WHATSAPP_ACCESS_TOKEN=<nuevo>
   WHATSAPP_DRY_RUN=0
   WHATSAPP_TEMPLATE_NAME=nexus_outreach_d7
   WHATSAPP_TEMPLATE_DAY7=nexus_outreach_d7
   WHATSAPP_TEMPLATE_LANGUAGE=es
   ```
6. Reiniciar backend → `GET /health/whatsapp?deep=true` → `api_reachable: true`, `dry_run: false`
7. Crear plantilla (abajo) → esperar aprobación → probar Día 7

## Plantilla Meta a crear (WhatsApp Manager → Plantillas)

| Campo | Valor |
|--------|--------|
| Nombre | `nexus_outreach_d7` |
| Categoría | Utility o Marketing (la que Meta permita para outreach) |
| Idioma | Español (`es`) |
| Cuerpo | ver copy |

### Copy del cuerpo (3 variables)

Nexus envía parámetros: `{{1}}` = nombre, `{{2}}` = empresa, `{{3}}` = mensaje de secuencia.

```
Hola {{1}}, te escribo de CostGuard / Nexus Sales respecto a {{2}}.

{{3}}

Si preferís, respondé acá y coordinamos 15 min.
```

Ejemplo con valores:

```
Hola María, te escribo de CostGuard / Nexus Sales respecto a Empresa Test Nexus.

Te escribo por WhatsApp porque no tuve respuesta al email: ¿te sirve una llamada breve esta semana para ver si Nexus Sales aporta a tu outbound?

Si preferís, respondé acá y coordinamos 15 min.
```

## Fallback demo (si Meta sigue trabado el jueves)

Dejar `WHATSAPP_DRY_RUN=1`. En Integraciones / historial se ve envío simulado. Pitch: “el canal está cableado; producción pendiente de Meta”.
