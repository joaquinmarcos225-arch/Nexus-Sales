"""
Banco determinístico de mensajes cold / follow-up (B2B + B2C).

Sin research ni inventos de industria: slots CRM + {valor} de la ficha de producto.
Email ≠ LinkedIn ≠ WhatsApp. Rotación estable por prospecto/campaña/canal/toque.

Reglas humanas (por nº de mensaje en la secuencia del prospecto):
1) Siempre enganchar a la persona/empresa ANTES del producto.
2) Al menos 1 vez por secuencia: preguntar cómo está.
3) Toque 1: explicar un poco más qué es / qué hace el producto (cualquier canal).
4) Toque 2+: apalancar el mensaje/canal anterior; desde el 3er toque, más fuerte
   («Retomo lo que te decía por …»).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.schemas.mvp_outreach import SdrReasoningRead

Channel = Literal["email", "linkedin", "whatsapp"]
Market = Literal["b2b", "b2c"]

# ---------------------------------------------------------------------------
# Subjects (email cold)
# ---------------------------------------------------------------------------

_EMAIL_SUBJECTS_B2B = (
    "{producto} para {empresa}",
    "Idea para {nombre}",
    "{marca} × {empresa}",
    "Propuesta breve: {producto}",
    "Propuesta breve para {empresa}",
)
_EMAIL_SUBJECTS_B2C = (
    "{producto} para vos",
    "Idea para {nombre}",
    "{marca} · {producto}",
    "Propuesta breve: {producto}",
    "Un espacio para hablar de {producto}",
)

# ---------------------------------------------------------------------------
# Templates — B2B email cold
# ---------------------------------------------------------------------------

_B2B_EMAIL_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. {te_escribo_por}

{valor}

Si te parece útil, ¿coordinamos una reunión breve para ver si encaja?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

¿Te interesaría una call corta esta semana para verlo juntos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} ({marca}). {valor}

¿Tiene sentido una reunión breve para ver si aplica a {empresa}?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te contacto por {empresa}.

{valor}

¿Podemos agendar un espacio en tu semana para mostrarte cómo lo hacemos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Una idea corta para {empresa}.

{valor}

Si te resuena, ¿te parece una videollamada esta semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_rol_en_empresa}, capaz te interesa esto.

{valor}

¿Te queda bien una videollamada corta para verlo juntos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Quiero ver si {producto} suma para {empresa}.

{valor}

¿Coordinamos un rato para repasarlo?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque {valor_clause}

¿Te gustaría una reunión breve para explorarlo?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te dejo un mensaje breve sobre cómo podemos ayudar a {empresa}.

{valor}

¿Tenés 15 minutos esta semana para ver si sirve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo por tu trabajo en {empresa}.

{valor}

¿Agendamos una reunión breve para verlo con calma?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

¿Están abiertos a revisar si {producto} puede ayudar a {empresa}? {valor}

Si sí, ¿te parece una call de 15 minutos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Trabajamos con empresas que necesitan lo que ofrece {producto}.

{valor}

¿Te interesa un meet corto para ver cómo aplicaría a {empresa}?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

Si esto puede ayudar a {empresa}, me gustaría contártelo con tiempo.

{valor}

¿Hablamos un rato?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. No busco cerrar nada por mail. Quiero entender si {producto} tiene sentido para {empresa} y en qué les ayudaría.

{valor}

¿Te queda un espacio esta semana para una reunión breve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. {te_escribo_por}

{valor}

¿Coordinamos una videollamada para ver si les suma?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

Si querés, lo vemos en una call esta semana, sin compromiso.

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Pensé en {empresa} porque {valor_clause}

¿Te parece útil una charla corta?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te propongo mostrarte {producto} y ver juntos si aplica a {empresa}.

{valor}

¿Qué día de esta semana te queda mejor para una reunión?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque me interesa entender el día a día de {empresa} y ver si desde {producto} podemos facilitarles algo concreto.

{valor}

Si te parece, ¿podemos charlar un rato para ver en qué les ayudaría de verdad?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. No te escribo solo para presentarte {producto}. Me interesa conocer un poco el contexto de {empresa} y ver si hay alguna forma en la que podamos ayudar de manera concreta, sin forzar un encaje que no exista.

{valor}

Si te resulta útil, ¿te parece una videollamada en algún momento de la semana para explorarlo con calma?

Saludos,
{sdr}""",
)

_B2B_EMAIL_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Seguí interesado en ver si {producto} puede ayudar a {empresa}.

Si te sirve, ¿podemos agendar una reunión breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido una call corta para retomarlo?

Quedo atento a lo que te quede mejor.""",
    """Hola {nombre},

{retomo}
No quiero saturar. Si en algún momento querés explorar {producto}, coordinamos un espacio.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para retomar esa conversación en una reunión breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
La idea sigue siendo una charla corta sobre {producto}, sin presión.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Si ahora no es el mejor momento para lo de {producto}, entiendo. Cuando quieras retomarlo, avísame y lo vemos.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

¿Hoy vale la pena agendar 15 minutos para ver si ayuda a {empresa}?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Sigue teniendo sentido explorar si puede ayudar a {empresa}, o preferís dejarlo para más adelante?

Quedo atento.""",
)

_B2B_LI_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te parece si coordinamos una charla corta esta semana?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{hook}

{valor}

¿Te queda bien una videollamada breve para verlo juntos?""",
    """Hola {nombre},

{sdr} de {marca} por acá. {como_estas}{hook}

{valor}

¿Tiene sentido una reunión corta esta semana?""",
    """Hola {nombre},

Te contacto desde {marca}. Soy {sdr}. {como_estas}{hook}

{valor}

¿Podemos coordinar 10 o 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

¿Te parece si agendamos 15 minutos para charlarlo?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

Si te resuena, ¿lo vemos en una llamada breve?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{hook}

{valor}

¿Te sirve una charla corta para explorar si aplica?""",
    """Hola {nombre},

Te escribo desde {marca}, soy {sdr}. {como_estas}{hook}

{valor}

¿Te queda bien una videollamada corta?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Coordinamos un meet rápido esta semana?""",
    """Hola {nombre},

{sdr} ({marca}). {como_estas}{hook}

{valor}

¿Te parece si hablamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

¿Agendamos una reunión breve para verlo con calma?""",
    """Hola {nombre},

Trabajo en {marca} y pensé en escribirte. {como_estas}{hook}

{valor}

¿Te sirve una llamada de 15 minutos?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te parece útil una charla corta?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

Si querés, ¿lo vemos en un meet corto esta semana?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{hook}

{valor}

¿Hablamos un rato para ver si encaja?""",
)

_B2B_LI_FU = (
    """Hola {nombre},

{retomo}
{como_estas}La idea sigue siendo ver si {producto} suma para {empresa}.

¿Te viene bien 15 minutos esta semana?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido retomar esa conversación en una llamada corta?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión: si ahora no es momento, lo dejamos. Si sí, ¿coordinamos 10 o 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para una charla breve sobre {producto}?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Quería dejarlo arriba por si el mensaje anterior no llegó en buen momento.

¿Seguís con ganas de explorarlo?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Vale la pena 15 minutos para {empresa} con {producto}, o preferís retomarlo más adelante?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo solo esto: {valor}

¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve, avisame y armamos un espacio corto.

Quedo atento.""",
)

_B2B_WA_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Tenés un rato esta semana para una llamada corta?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{te_escribo_por_corto}

{valor}

¿Agendamos una charla breve?""",
    """Hola {nombre},

{sdr} de {marca} por acá. {como_estas}{te_escribo_por_corto}

{valor}

¿Te sirve una videollamada corta?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Hablamos un rato corto?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{te_escribo_por_corto}

{valor}

¿Lo vemos en una call de 15 minutos?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

Si te interesa, ¿coordinamos una llamada corta?""",
    """Hola {nombre},

{como_estas}Soy {sdr} ({marca}). {te_escribo_por_corto}

{valor}

¿Te parece si agendamos 15 minutos?""",
    """Hola {nombre},

Trabajo en {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Te queda una llamada breve?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{te_escribo_por_corto}

{valor}

¿Hacemos una charla corta para ver si aplica a {empresa}?""",
    """Hola {nombre},

{sdr} acá. {como_estas}{te_escribo_por_corto}

{valor}

¿Tiene sentido una reunión breve?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Coordinamos 15 minutos para charlarlo?""",
    """Hola {nombre},

{como_estas}{te_escribo_por_corto}

{valor}

Soy {sdr}. ¿Hablamos un rato para verlo?""",
    """Hola {nombre},

{sdr} ({marca}). {como_estas}{te_escribo_por_corto}

{valor}

¿Agendamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{te_escribo_por_corto}

{valor}

Si querés lo vemos rápido en una llamada corta.""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Te parece una videollamada breve esta semana?""",
)

_B2B_WA_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve una llamada corta sobre {producto}, avisame.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor hoy o otro día para 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión. Si más adelante querés ver {producto}, escribime.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
¿Tiene sentido para {empresa} seguir explorando {producto}?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

Si no es momento, lo retomamos después.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Esta semana o la próxima para una llamada breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Seguís con ganas de ver {producto} o preferís dejarlo para más adelante?

Quedo atento.""",
)

# ---------------------------------------------------------------------------
# Templates — B2C
# ---------------------------------------------------------------------------

_B2C_EMAIL_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque me interesa ver si podemos ayudarte con {producto}.

{valor}

Si te parece útil, ¿coordinamos una charla breve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

¿Te interesaría una call corta esta semana para verlo juntos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} ({marca}). {valor}

¿Tiene sentido una reunión breve para ver si te sirve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te contacto para ver si {producto} puede facilitarte algo concreto.

{valor}

¿Podemos agendar un espacio en tu semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Una idea que capaz te suma.

{valor}

Si te resuena, ¿te parece una videollamada esta semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Pensé en escribirte porque capaz {producto} te ayuda de verdad.

{valor}

¿Te queda bien una videollamada corta?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Quiero ver si {producto} te suma, sin forzar nada.

{valor}

¿Coordinamos un rato para repasarlo?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque {valor_clause}

¿Te gustaría una reunión breve para explorarlo?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te dejo un mensaje breve sobre cómo podemos ayudarte.

{valor}

¿Tenés 15 minutos esta semana para ver si te sirve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo con ganas de entender tu situación y ver si encajamos.

{valor}

¿Agendamos una reunión breve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

¿Estás abierto a revisar si {producto} puede ayudarte? {valor}

Si sí, ¿te parece una call de 15 minutos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Trabajamos con personas que buscan lo que ofrece {producto}.

{valor}

¿Te interesa un meet corto para ver cómo te aplicaría?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

Si esto puede ayudarte, me gustaría contártelo con tiempo.

{valor}

¿Hablamos un rato?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. No busco cerrar nada por mail. Quiero ver si {producto} tiene sentido para vos y en qué te ayudaría.

{valor}

¿Te queda un espacio esta semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo con interés genuino en ver si podemos facilitarte algo con {producto}.

{valor}

¿Coordinamos una videollamada?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

Si querés, lo vemos en una call esta semana, sin compromiso.

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Pensé en vos porque {valor_clause}

¿Te parece útil una charla corta?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te propongo mostrarte {producto} y ver juntos si te aplica.

{valor}

¿Qué día de esta semana te queda mejor para una reunión?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque me interesa entender tu día a día y ver si desde {producto} podemos facilitarte algo concreto.

{valor}

Si te parece, ¿podemos charlar un rato para ver en qué te ayudaría de verdad?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. No te escribo solo para presentarte {producto}. Me interesa conocer un poco tu contexto y ver si hay alguna forma en la que podamos ayudar de manera concreta, sin forzar un encaje que no exista.

{valor}

Si te resulta útil, ¿te parece una videollamada en algún momento de la semana para explorarlo con calma?

Saludos,
{sdr}""",
)

_B2C_EMAIL_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Seguí interesado en ver si {producto} puede ayudarte.

Si te sirve, ¿podemos agendar una reunión breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido una call corta para retomarlo?

Quedo atento a lo que te quede mejor.""",
    """Hola {nombre},

{retomo}
No quiero saturar. Si en algún momento querés explorar {producto}, coordinamos un espacio.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para retomar esa conversación?

Quedo atento.""",
    """Hola {nombre},

{retomo}
La idea sigue siendo una charla corta sobre {producto}, sin presión.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Si ahora no es el mejor momento para lo de {producto}, entiendo. Cuando quieras retomarlo, avísame.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

¿Hoy vale la pena agendar 15 minutos para ver si te ayuda?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Sigue teniendo sentido explorarlo, o preferís dejarlo para más adelante?

Quedo atento.""",
)

_B2C_LI_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te parece si coordinamos una charla corta esta semana?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{hook}

{valor}

¿Te queda bien una videollamada breve?""",
    """Hola {nombre},

{sdr} de {marca} por acá. {como_estas}{hook}

{valor}

¿Tiene sentido una reunión corta esta semana?""",
    """Hola {nombre},

Te contacto desde {marca}. Soy {sdr}. {como_estas}{hook}

{valor}

¿Podemos coordinar 10 o 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

¿Te parece si agendamos 15 minutos para charlarlo?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

Si te resuena, ¿lo vemos en una llamada breve?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{hook}

{valor}

¿Te sirve una charla corta para explorarlo?""",
    """Hola {nombre},

Te escribo desde {marca}, soy {sdr}. {como_estas}{hook}

{valor}

¿Te queda bien una videollamada corta?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Coordinamos un meet rápido esta semana?""",
    """Hola {nombre},

{sdr} ({marca}). {como_estas}{hook}

{valor}

¿Te parece si hablamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

¿Agendamos una reunión breve para verlo con calma?""",
    """Hola {nombre},

Trabajo en {marca} y pensé en escribirte. {como_estas}{hook}

{valor}

¿Te sirve una llamada de 15 minutos?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te parece útil una charla corta?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

Si querés, ¿lo vemos en un meet corto esta semana?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{hook}

{valor}

¿Hablamos un rato para ver si te aplica?""",
)

_B2C_LI_FU = (
    """Hola {nombre},

{retomo}
{como_estas}La idea sigue siendo ver si {producto} te suma.

¿Te viene bien 15 minutos esta semana?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido retomar esa conversación en una llamada corta?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión: si ahora no es momento, lo dejamos. Si sí, ¿coordinamos 10 o 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para una charla breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Quería dejarlo arriba por si el mensaje anterior no llegó en buen momento.

¿Seguís con ganas de explorarlo?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Vale la pena 15 minutos con {producto}, o preferís retomarlo más adelante?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo solo esto: {valor}

¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve, avisame y armamos un espacio corto.

Quedo atento.""",
)

_B2C_WA_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Tenés un rato esta semana para una llamada corta?""",
    """Hola {nombre},

Soy {sdr} ({marca}). {como_estas}{te_escribo_por_corto}

{valor}

¿Agendamos una charla breve?""",
    """Hola {nombre},

{sdr} de {marca} por acá. {como_estas}{te_escribo_por_corto}

{valor}

¿Te sirve una videollamada corta?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Hablamos un rato corto?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{te_escribo_por_corto}

{valor}

¿Lo vemos en una call de 15 minutos?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

Si te interesa, ¿coordinamos una llamada corta?""",
    """Hola {nombre},

{como_estas}Soy {sdr} ({marca}). {te_escribo_por_corto}

{valor}

¿Agendamos 15 minutos?""",
    """Hola {nombre},

Trabajo en {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Te queda una llamada breve?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{te_escribo_por_corto}

{valor}

¿Hacemos una charla corta para ver si te aplica?""",
    """Hola {nombre},

{sdr} acá. {como_estas}{te_escribo_por_corto}

{valor}

¿Tiene sentido una reunión breve?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Coordinamos 15 minutos?""",
    """Hola {nombre},

{como_estas}{te_escribo_por_corto}

{valor}

Soy {sdr}. ¿Hablamos un rato para verlo?""",
    """Hola {nombre},

{sdr} ({marca}). {como_estas}{te_escribo_por_corto}

{valor}

¿Agendamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{te_escribo_por_corto}

{valor}

Si querés lo vemos rápido en una llamada corta.""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{te_escribo_por_corto}

{valor}

¿Te parece una videollamada breve esta semana?""",
)

_B2C_WA_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve una llamada corta sobre {producto}, avisame.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor hoy o otro día para 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión. Si más adelante querés ver {producto}, escribime.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
¿Tiene sentido seguir explorando {producto}?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

Si no es momento, lo retomamos después.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Esta semana o la próxima para una llamada breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Seguís con ganas de ver {producto} o preferís dejarlo para más adelante?

Quedo atento.""",
)

_BANKS: dict[tuple[Market, Channel, str], tuple[str, ...]] = {
    ("b2b", "email", "cold"): _B2B_EMAIL_COLD,
    ("b2b", "email", "fu"): _B2B_EMAIL_FU,
    ("b2b", "linkedin", "cold"): _B2B_LI_COLD,
    ("b2b", "linkedin", "fu"): _B2B_LI_FU,
    ("b2b", "whatsapp", "cold"): _B2B_WA_COLD,
    ("b2b", "whatsapp", "fu"): _B2B_WA_FU,
    ("b2c", "email", "cold"): _B2C_EMAIL_COLD,
    ("b2c", "email", "fu"): _B2C_EMAIL_FU,
    ("b2c", "linkedin", "cold"): _B2C_LI_COLD,
    ("b2c", "linkedin", "fu"): _B2C_LI_FU,
    ("b2c", "whatsapp", "cold"): _B2C_WA_COLD,
    ("b2c", "whatsapp", "fu"): _B2C_WA_FU,
}


@dataclass(frozen=True)
class ColdBankRender:
    subject: str | None
    body: str
    template_id: str
    market: Market
    channel: Channel
    kind: str  # cold | fu
    index: int
    leverage_used: bool
    reasoning: SdrReasoningRead


def _stable_index(*, seed: str, n: int) -> int:
    if n <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % n


def _norm_channel(channel: str) -> Channel:
    ch = (channel or "email").strip().lower()
    if ch in ("linkedin", "li"):
        return "linkedin"
    if ch in ("whatsapp", "wa"):
        return "whatsapp"
    return "email"


def _norm_market(campaign: dict[str, Any] | None, *, explicit: str | None = None) -> Market:
    raw = (explicit or "").strip().lower()
    if not raw and campaign:
        raw = str(
            campaign.get("outreach_mode")
            or campaign.get("market")
            or campaign.get("mode")
            or ""
        ).strip().lower()
    return "b2c" if raw == "b2c" else "b2b"


def _first_name(prospect: dict[str, Any]) -> str:
    from app.services.outreach_display_names import prospect_greeting_name

    return prospect_greeting_name(prospect) or "hola"


def _sender(campaign: dict[str, Any]) -> str:
    from app.services.outreach_display_names import sender_first_name

    return sender_first_name(
        campaign_sender=campaign.get("sender_name"),
        fallback="el equipo",
    )


def _brand(campaign: dict[str, Any]) -> str:
    from app.services.outreach_display_names import outreach_company_display

    for key in ("brand_name", "company_name", "seller_company_name"):
        brand = outreach_company_display(campaign.get(key)) or ""
        if brand:
            return brand
    return "nuestro equipo"


def _product_name(product: dict[str, Any] | None, *, brand: str) -> str:
    name = ((product or {}).get("name") or "").strip()
    return name or brand or "nuestra solución"


def _valor_one_liner(
    product: dict[str, Any] | None,
    *,
    product_name: str,
    market: Market,
    channel: str = "email",
    explain_more: bool = False,
) -> str:
    """
    Valor desde ficha, moldeado para leer bien en el mensaje.
    Si explain_more (1.er toque de secuencia): qué es + qué hace, un poco más largo
    (excepto WhatsApp: siempre corto/chill).
    """
    from app.services.message_structure_variants import _conversational_value_blurb

    ch = _norm_channel(channel)
    # WA siempre corto; LI/email pueden alargar un poco en el 1.er toque de secuencia.
    blurb_channel = "email" if (explain_more and ch != "whatsapp") else ch
    blurb = (
        _conversational_value_blurb(
            product, product_name=product_name, channel=blurb_channel
        )
        or ""
    ).strip()
    desc = re.sub(
        r"\s+", " ", ((product or {}).get("description") or "").strip()
    ).rstrip(".")
    if explain_more and ch != "whatsapp":
        parts: list[str] = []
        if blurb and len(blurb) >= 12:
            parts.append(blurb if blurb.endswith((".", "!", "?")) else f"{blurb}.")
        if desc and len(desc) >= 20:
            # Evitar repetir casi lo mismo que el blurb.
            if blurb.lower()[:40] not in desc.lower() and desc.lower()[:40] not in blurb.lower():
                clause = desc[0].lower() + desc[1:] if desc else desc
                parts.append(
                    f"En concreto, {clause}."
                    if not clause.endswith((".", "!", "?"))
                    else f"En concreto, {clause}"
                )
        if parts:
            return " ".join(parts)
    if blurb and len(blurb) >= 12:
        return blurb if blurb.endswith((".", "!", "?")) else f"{blurb}."
    if market == "b2c":
        return f"Con {product_name} podemos ayudarte de forma concreta."
    return f"Con {product_name} podemos ayudar de forma concreta."


def _lowercase_first(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    return t[0].lower() + t[1:] if t[0].isupper() else t


def _valor_clause(valor: str) -> str:
    """Para unir tras 'porque' / similar — sin dos puntos."""
    t = (valor or "").strip()
    if not t:
        return "podemos aportar valor de forma concreta."
    t = _lowercase_first(t)
    return t if t.endswith((".", "!", "?")) else f"{t}."


_HOW_ARE_YOU_RE = re.compile(
    r"\b(c[oó]mo est[aá]s|c[oó]mo va|qu[eé] tal)\b",
    re.I,
)


def _prior_asked_how_are_you(prior_touches: list[dict[str, Any]] | None) -> bool:
    for t in prior_touches or []:
        body = str(t.get("body") or t.get("message_body") or "")
        if _HOW_ARE_YOU_RE.search(body):
            return True
    return False


def _channel_label(channel: Channel) -> str:
    if channel == "linkedin":
        return "LinkedIn"
    if channel == "whatsapp":
        return "WhatsApp"
    return "mail"


def _hook_line(*, market: Market, empresa: str, rol: str, seed: str) -> str:
    """Enganche humano ANTES del producto (nunca pitch de entrada)."""
    if market == "b2c":
        opts = (
            "Te escribo porque me interesa entender un poco tu contexto antes de proponerte nada.",
            "Quería saludarte y ver si tiene sentido charlar un momento.",
            "Te contacto con una idea corta, pero primero quería conectar.",
        )
    else:
        if rol and rol != "tu rol":
            opts = (
                f"Te escribo por tu rol como {rol} en {empresa}.",
                f"Vi tu perfil y pensé en {empresa}.",
                f"Te contacto por tu trabajo como {rol} en {empresa}.",
                f"Quería escribirte por {empresa}, sin ir directo al pitch.",
            )
        else:
            opts = (
                f"Te escribo por tu trabajo en {empresa}.",
                f"Vi {empresa} y pensé en contactarte.",
                f"Te contacto por {empresa}, sin ir directo al pitch.",
                f"Quería escribirte por {empresa} y entender un poco el contexto.",
            )
    idx = _stable_index(seed=seed + "|hook", n=len(opts))
    return opts[idx]


def _retomo_line(
    *,
    prior_touches: list[dict[str, Any]] | None,
    current_channel: Channel,
    producto: str,
    strength: Literal["soft", "strong"] = "soft",
) -> str:
    """Apalanca mensajes/canales previos. strong = toques 3+ de la secuencia."""
    prior = prior_touches or []
    if not prior:
        return ""
    last = prior[-1]
    last_ch = _norm_channel(str(last.get("channel") or "email"))
    label = _channel_label(last_ch)
    if strength == "strong":
        if last_ch != current_channel:
            return f"Retomo lo que te decía por {label} sobre {producto}."
        return f"Retomo lo que te comentaba sobre {producto}."
    if last_ch != current_channel:
        return f"Te había escrito por {label} sobre {producto}."
    if last_ch == "email":
        return f"Retomo el mail anterior sobre {producto}."
    return f"Vuelvo sobre el mensaje anterior de {producto}."


def _slots(
    *,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
    market: Market,
    channel: str = "email",
    prior_touches: list[dict[str, Any]] | None = None,
    explain_more: bool = False,
    ask_how_are_you: bool = False,
    retomo_strength: Literal["soft", "strong"] = "soft",
    seed: str = "",
) -> dict[str, str]:
    nombre = _first_name(prospect)
    if nombre.lower() == "hola":
        nombre = (prospect.get("name") or "").strip().split()[0] if (prospect.get("name") or "").strip() else "allí"
    sdr = _sender(campaign)
    brand = _brand(campaign)
    empresa = (prospect.get("company_name") or "").strip() or "tu empresa"
    rol = (prospect.get("role") or prospect.get("selling_to_role") or "").strip()
    producto = _product_name(product, brand=brand)
    valor = _valor_one_liner(
        product,
        product_name=producto,
        market=market,
        channel=channel,
        explain_more=explain_more,
    )

    if rol:
        te_escribo_por = f"Te escribo por tu rol como {rol} en {empresa}."
        te_escribo_por_corto = f"Te escribo por tu rol como {rol}."
        como_rol_en_empresa = f"Como {rol} en {empresa}"
        rol_en = f"como {rol} en {empresa}"
    else:
        te_escribo_por = f"Te escribo por tu trabajo en {empresa}."
        te_escribo_por_corto = f"Te escribo por {empresa}."
        como_rol_en_empresa = f"En {empresa}"
        rol_en = f"en {empresa}"

    hook = _hook_line(market=market, empresa=empresa, rol=rol or "tu rol", seed=seed or producto)
    como_estas = "¿Cómo estás? " if ask_how_are_you else ""
    retomo = _retomo_line(
        prior_touches=prior_touches,
        current_channel=_norm_channel(channel),
        producto=producto,
        strength=retomo_strength,
    )
    if retomo and not retomo.endswith((".", "!", "?")):
        retomo = f"{retomo}."
    if retomo:
        retomo = f"{retomo} "

    return {
        "nombre": nombre,
        "empresa": empresa,
        "rol": rol or "tu rol",
        "rol_en": rol_en,
        "sdr": sdr,
        "marca": brand,
        "producto": producto,
        "valor": valor,
        "valor_clause": _valor_clause(valor),
        "te_escribo_por": te_escribo_por,
        "te_escribo_por_corto": te_escribo_por_corto,
        "como_rol_en_empresa": como_rol_en_empresa,
        "hook": hook,
        "como_estas": como_estas,
        "retomo": retomo,
    }


def _fill(template: str, slots: dict[str, str]) -> str:
    out = template
    for key, val in slots.items():
        out = out.replace("{" + key + "}", val)
    # Limpieza suave de dobles espacios / rol vacío residual.
    out = re.sub(r"como tu rol en ", "en ", out, flags=re.I)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip()


def _leverage_line(
    *,
    prior_touches: list[dict[str, Any]] | None,
    current_channel: Channel,
    producto: str,
    seed: str,
    force: bool = False,
    strength: Literal["soft", "strong"] = "soft",
) -> str:
    prior = prior_touches or []
    others = [
        t
        for t in prior
        if _norm_channel(str(t.get("channel") or "")) != current_channel
    ]
    # Si no hay otro canal, igual podemos apalancar el mismo canal en FU.
    if not others and not (force and prior):
        return ""
    if not force and _stable_index(seed=seed + "|lev", n=100) >= 35:
        return ""
    return _retomo_line(
        prior_touches=prior,
        current_channel=current_channel,
        producto=producto,
        strength=strength,
    ).rstrip()


def _insert_leverage(body: str, *, channel: Channel, line: str) -> str:
    if not line:
        return body
    # Evitar duplicar si la plantilla ya trae {retomo}.
    if line.rstrip(".")[:24].lower() in body.lower():
        return body
    if channel == "email" or "\n\n" in body:
        parts = body.split("\n\n", 2)
        if len(parts) >= 3:
            return f"{parts[0]}\n\n{parts[1]}\n\n{line}\n\n{parts[2]}"
        if len(parts) == 2:
            return f"{parts[0]}\n\n{line}\n\n{parts[1]}"
        return f"{body}\n\n{line}"
    m = re.search(r"([.!?])\s+", body)
    if not m:
        return f"{body} {line}"
    i = m.end()
    return f"{body[:i]}{line} {body[i:]}"


def _inject_como_estas_email(body: str, como_estas: str) -> str:
    """Inserta ¿Cómo estás? en mails cold que no usan el slot."""
    if not como_estas or "cómo estás" in body.lower() or "como estas" in body.lower():
        return body
    # Tras el saludo "Hola X," + bloque "Soy …"
    parts = body.split("\n\n", 1)
    if len(parts) != 2:
        return body
    return f"{parts[0]}\n\n{como_estas.strip()}\n\n{parts[1]}"


def first_touch_on_channel(
    prior_touches: list[dict[str, Any]] | None,
    channel: str,
) -> bool:
    """True si aún no hubo outbound en este canal (puede haber otros canales)."""
    ch = _norm_channel(channel)
    for t in prior_touches or []:
        if _norm_channel(str(t.get("channel") or "")) == ch:
            return False
    return True


def pick_bank_index(
    *,
    market: Market,
    channel: Channel,
    kind: str,
    prospect_id: int | str | None,
    campaign_id: int | str | None,
    step_day: int = 0,
) -> int:
    bank = _BANKS[(market, channel, kind)]
    extra = "cold" if kind == "cold" else f"fu|{int(step_day or 0)}"
    seed = f"{prospect_id or 0}|{campaign_id or 0}|{channel}|{market}|{extra}"
    return _stable_index(seed=seed, n=len(bank))


def render_cold_bank_touch(
    *,
    channel: str,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
    prior_touches: list[dict[str, Any]] | None = None,
    first_touch: bool | None = None,
    step_day: int = 0,
    outreach_mode: str | None = None,
) -> ColdBankRender:
    ch = _norm_channel(channel)
    market = _norm_market(campaign, explicit=outreach_mode)
    prior = list(prior_touches or [])
    # Nº de mensaje en la secuencia (1 = primero del prospecto, sin importar canal).
    touch_n = len(prior) + 1
    if first_touch is None:
        first_touch = first_touch_on_channel(prior, ch)
    kind = "cold" if first_touch else "fu"
    bank = _BANKS[(market, ch, kind)]
    pid = prospect.get("id")
    cid = campaign.get("id") or campaign.get("campaign_id")
    idx = pick_bank_index(
        market=market,
        channel=ch,
        kind=kind,
        prospect_id=pid,
        campaign_id=cid,
        step_day=step_day,
    )
    seed = f"{pid or 0}|{cid or 0}|{ch}|{market}|{kind}|{idx}|n{touch_n}"
    explain_more = touch_n == 1
    # Mínimo 1 vez por secuencia: en el 1.er toque; si no quedó, en el 2.º.
    ask_how = (not _prior_asked_how_are_you(prior)) and touch_n <= 2
    retomo_strength: Literal["soft", "strong"] = "strong" if touch_n >= 3 else "soft"

    slots = _slots(
        prospect=prospect,
        campaign=campaign,
        product=product,
        market=market,
        channel=ch,
        prior_touches=prior,
        explain_more=explain_more,
        ask_how_are_you=ask_how,
        retomo_strength=retomo_strength,
        seed=seed,
    )
    body = _fill(bank[idx], slots)

    if kind == "cold" and ch == "email" and slots.get("como_estas"):
        body = _inject_como_estas_email(body, slots["como_estas"])

    lev = ""
    # Toque 2+: siempre apalancar (cross-canal o mismo canal). Cold cross-canal también.
    if touch_n >= 2 or (kind == "cold" and prior):
        # Las plantillas FU ya traen {retomo}; no duplicar si ya está.
        if "{retomo}" not in bank[idx] and "retomo" not in bank[idx].lower():
            lev = _leverage_line(
                prior_touches=prior,
                current_channel=ch,
                producto=slots["producto"],
                seed=seed,
                force=touch_n >= 2,
                strength=retomo_strength,
            )
            if lev:
                body = _insert_leverage(body, channel=ch, line=lev)
    elif kind == "cold":
        lev = _leverage_line(
            prior_touches=prior,
            current_channel=ch,
            producto=slots["producto"],
            seed=seed,
            force=False,
            strength="soft",
        )
        if lev:
            body = _insert_leverage(body, channel=ch, line=lev)

    # Guardrail: nunca empezar el pitch de producto justo después del saludo corto.
    body = _ensure_hook_before_product(body, slots=slots, channel=ch)

    subject: str | None = None
    if ch == "email" and kind == "cold":
        subjects = _EMAIL_SUBJECTS_B2C if market == "b2c" else _EMAIL_SUBJECTS_B2B
        s_idx = _stable_index(seed=seed + "|subj", n=len(subjects))
        subject = _fill(subjects[s_idx], slots)
    elif ch == "email" and kind == "fu":
        subject = f"Re: {slots['producto']}"

    tid = f"{market}-{ch}-{kind}-{idx + 1:02d}"
    reasoning = SdrReasoningRead(
        probable_problem=slots["valor"][:180],
        why_it_matters=(
            f"Plantilla {tid} · toque #{touch_n} · "
            f"{'explica producto' if explain_more else 'apalanca previo'}"
        ),
        hypothesis=slots["producto"],
        response_question="coordinar reunión / call / espacio",
        selling_to_role=(prospect.get("role") or "") if market == "b2b" else "",
    )
    return ColdBankRender(
        subject=subject,
        body=body,
        template_id=tid,
        market=market,
        channel=ch,
        kind=kind,
        index=idx,
        leverage_used=bool(lev) or bool(slots.get("retomo", "").strip()),
        reasoning=reasoning,
    )


def _ensure_hook_before_product(
    body: str, *, slots: dict[str, str], channel: Channel
) -> str:
    """Si el cuerpo va saludo → pitch de producto, inserta el hook."""
    text = (body or "").strip()
    if not text:
        return text
    hook = (slots.get("hook") or "").strip()
    if not hook:
        return text
    # Ya hay enganche típico.
    low = text.lower()
    if any(
        x in low
        for x in (
            "te escribo por",
            "te contacto",
            "pensé en",
            "vi tu",
            "por tu rol",
            "por tu trabajo",
            "entender",
            "contexto",
            "sin ir directo",
        )
    ):
        return text
    producto = (slots.get("producto") or "").strip()
    valor = (slots.get("valor") or "").strip()
    # Detectar "Hola X, soy Y. Con Producto…"
    m = re.match(
        r"^(Hola\s+[^.,!?]+[.,!]?\s*(?:Soy\s+[^.]+\.\s*)?)(Con\s+)",
        text,
        flags=re.I,
    )
    if m and producto and producto.lower() in text.lower()[:160]:
        return f"{m.group(1)}{hook} {m.group(2)}{text[m.end():]}"
    # LI/WA: valor pegado demasiado pronto.
    if channel in ("linkedin", "whatsapp") and valor and valor[:40].lower() in low[:160]:
        m2 = re.match(r"^(Hola\s+[^.]+\.\s*(?:Soy\s+[^.]+\.\s*)?)", text, flags=re.I)
        if m2 and hook.lower() not in low:
            return f"{m2.group(1)}{hook} {text[m2.end():]}"
    return text

def bank_counts() -> dict[str, int]:
    return {f"{m}-{c}-{k}": len(v) for (m, c, k), v in _BANKS.items()}
