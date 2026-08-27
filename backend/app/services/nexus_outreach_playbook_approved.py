"""
Playbook SDR aprobado — referencias de tono y estructura por día/canal.
La IA adapta nombres, empresa vendedora ([Tuempresa]) y producto ([Producto]); no copia literal.
Personalización + problema/beneficio + CTA a reunión. Nunca cerrar la venta.
"""

from __future__ import annotations

# Día 1 · Email — primer contacto personalizado
DAY1_EMAIL_REFERENCE = """
Asunto ejemplo: Automatización de prospección para [Empresa del prospecto]
(Breve, directo, curiosidad — sin spam comercial.)

Cuerpo ejemplo (SOLO si hay research confirmado en el brief):
Hola, [Nombre]
Soy [SDR]. Vi que en [Empresa] están enfocados en [dato confirmado del brief].

Habitualmente, los directores de ventas en tu sector pierden horas semanales en tareas manuales de contacto en lugar de cerrar acuerdos. Ayudamos a empresas como la tuya a automatizar ese proceso para multiplicar reuniones mensuales.

¿Te queda bien una llamada corta este jueves a las 10:00 a. m. para ver cómo lo hacemos?
Saludos,
[SDR]

Si NO hay research confirmado, el gancho debe ser CRM suave:
"Soy [SDR]. Te escribo por tu rol en [Empresa]." — sin inventar LinkedIn/crecimiento/news.

Reglas Día 1 Email:
- Gancho con INVESTIGACIÓN confirmada (empresa B2B / persona B2C). Si no hay dato: empresa o rol CRM, sin inventar.
- Problema sectorial + solución del Producto (beneficio, no features).
- CTA reunión corta. NUNCA cerrar venta.
- Largo similar al ejemplo (~70–110 palabras). PROHIBIDO relleno.
"""

# Día 1 · LinkedIn — primer contacto (si el plan arranca en LI o toque LI sin historial)
DAY1_LINKEDIN_REFERENCE = """
Con research confirmado:
Hola, [Nombre]

Soy [SDR]. [1 frase con dato confirmado de [Empresa]/rol].]

Ayudamos a [rol] en tu sector a [beneficio concreto del Producto], eliminando [dolor clave].

¿Te queda bien una videollamada corta esta semana?

Sin research: "Soy [SDR]. Te escribo por tu rol en [Empresa]." — NUNCA inventes crecimiento ni news.

Reglas: más corto y directo que email. Gancho solo con evidencia + problema/solución + CTA.
"""

# Día 1 · WhatsApp — primer contacto ultra breve
DAY1_WHATSAPP_REFERENCE = """
Hola, [Nombre]. Soy [SDR].

Te escribo porque ayudamos a empresas de tu sector a [beneficio concreto del Producto].

¿Tendrás 5 minutos libres este jueves a las 11:00 am para una llamada rápida? Te muestro brevemente cómo lo logramos.

Reglas: 30–50 palabras, un solo bloque, informal, legible de un vistazo.
"""

# Día 4 · LinkedIn — seguimiento sin culpa
DAY4_LINKEDIN_REFERENCE = """
Hola, [Nombre]
Paso rápido por aquí para dejar esto arriba en tu bandeja.
Olvidé comentarte que implementamos este método con equipos de empresas similares, logrando acelerar un 30% en solo un mes.
¿Te queda bien una llamada corta este jueves a las 11:00 a. m. para ver si aplica a tu equipo?
Saludos
"""

# Día 7 · WhatsApp — seguimiento facilitar agenda
DAY7_WHATSAPP_REFERENCE = """
Hola, [Nombre]. Espero que estés teniendo una buena semana.

Te escribo por aquí para ver si logramos coincidir en una llamada corta de 5 minutos.

¿Te queda mejor esta tarde o preferís que lo revisemos el próximo lunes? Quedo atento.
"""

# Día 10 · Email — nuevo ángulo / mismo hilo
DAY10_EMAIL_REFERENCE = """
Asunto: Re: [mismo asunto del correo 1]

Hola, [Nombre]
Te escribo brevemente para dejar esto arriba en tu bandeja.
Olvidé mencionarte que hace poco ayudamos a empresas similares a implementar este sistema, logrando que su equipo comercial agendara 15 reuniones nuevas en su primer mes.
¿Te queda bien una videollamada breve este martes a las 4:00 p. m.?
Saludos,
[SDR]
"""

# Día 13 · LinkedIn — reactivar con recurso (NUNCA "¿pudiste leer?")
DAY13_LINKEDIN_REFERENCE = """
Hola, [Nombre]
Paso rápido por aquí para dejar esto arriba.
Olvidé comentarte que con equipos similares logramos [beneficio distinto / dato nuevo] en pocas semanas.
¿Te queda bien una llamada corta esta semana para ver si aplica a tu equipo?
Saludos
"""

# Día 16 · WhatsApp — break-up suave
DAY16_WHATSAPP_REFERENCE = """
Hola [Nombre], ¿cómo va? Asumo que estás con otras prioridades en este momento.
Si más adelante querés que veamos [Producto] para [Empresa], avisame y coordinamos.
¡Un abrazo!
[SDR] de [Tuempresa].
"""

# Día 19 · Email — cierre definitivo
DAY19_EMAIL_REFERENCE = """
Asunto ejemplo: Prospección en [Empresa]

Hola, [Nombre]
Te escribo por última vez para no ocupar más espacio en tu bandeja. Asumo que no es el timing indicado por ahora.
Más adelante volveré a escribirte por si las prioridades cambian. Si preferís que lo veamos antes, avisame por acá.
Saludos,
[SDR]
"""

PLAYBOOK_REFERENCE_BY_STEP: dict[tuple[int, str], str] = {
    (1, "email"): DAY1_EMAIL_REFERENCE,
    (1, "linkedin"): DAY1_LINKEDIN_REFERENCE,
    (1, "whatsapp"): DAY1_WHATSAPP_REFERENCE,
    (4, "linkedin"): DAY4_LINKEDIN_REFERENCE,
    (7, "whatsapp"): DAY7_WHATSAPP_REFERENCE,
    (10, "email"): DAY10_EMAIL_REFERENCE,
    (13, "linkedin"): DAY13_LINKEDIN_REFERENCE,
    (16, "whatsapp"): DAY16_WHATSAPP_REFERENCE,
    (19, "email"): DAY19_EMAIL_REFERENCE,
}

# Fallback por canal cuando el plan del usuario usa (día, canal) fuera del mapa default.
_CHANNEL_FALLBACK: dict[str, str] = {
    "email": DAY1_EMAIL_REFERENCE,
    "linkedin": DAY1_LINKEDIN_REFERENCE,
    "whatsapp": DAY1_WHATSAPP_REFERENCE,
}


def approved_playbook_reference(*, step_day: int, channel: str) -> str:
    ch = str(channel or "").strip().lower()
    key = (int(step_day), ch)
    block = PLAYBOOK_REFERENCE_BY_STEP.get(key) or _CHANNEL_FALLBACK.get(ch, "")
    if not block:
        return ""
    return f"\nREFERENCIA APROBADA DEL PLAYBOOK (adaptá, no copies literal):\n{block.strip()}\n"
