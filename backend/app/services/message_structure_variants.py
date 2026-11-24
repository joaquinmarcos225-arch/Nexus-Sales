"""
Variantes de estructura outbound (primer toque + follow-up).

Selección automática y estable por prospecto/canal/campaña.
Bloques grandes editables — sin plantillas rígidas tipo
«ayudamos a equipos comerciales a {beneficio}».
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

Channel = Literal["email", "linkedin", "whatsapp"]
FirstTouchVariant = Literal["problem_offer", "context_value", "direct_short", "sincere_fit"]
FollowUpVariant = Literal["soft_summary", "soft_invite"]

FIRST_TOUCH_VARIANTS: tuple[FirstTouchVariant, ...] = (
    "problem_offer",
    "context_value",
    "direct_short",
    "sincere_fit",
)
FOLLOW_UP_VARIANTS: tuple[FollowUpVariant, ...] = (
    "soft_summary",
    "soft_invite",
)

_FIRST_LABELS = {
    "problem_offer": "E1/L1/W1 — Problema → oferta → resultado → CTA",
    "context_value": "E2/L2/W2 — Contexto personalizado → valor → CTA",
    "direct_short": "E3/L3/W3 — Directo / corto → valor en una idea → CTA",
    "sincere_fit": "E4/L4/W4 — Producto → encaje sincero → CTA reunión humilde",
}
_FOLLOW_LABELS = {
    "soft_summary": "EF1/LF1/WF1 — Resumen corto + puerta abierta + quedo atento",
    "soft_invite": "EF2/LF2/WF2 — Invitación suave / pregunta + quedo atento",
}


def _stable_index(*, seed: str, n: int) -> int:
    if n <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % n


def _seed_parts(
    *,
    prospect_id: int | str | None,
    channel: str,
    campaign_id: int | str | None = None,
    extra: str = "",
) -> str:
    pid = str(prospect_id if prospect_id is not None else "0")
    cid = str(campaign_id if campaign_id is not None else "0")
    ch = (channel or "email").strip().lower()
    return f"{pid}|{cid}|{ch}|{extra}"


def pick_first_touch_variant(
    *,
    channel: str,
    prospect_id: int | str | None = None,
    campaign_id: int | str | None = None,
) -> FirstTouchVariant:
    idx = _stable_index(
        seed=_seed_parts(
            prospect_id=prospect_id,
            channel=channel,
            campaign_id=campaign_id,
            extra="first",
        ),
        n=len(FIRST_TOUCH_VARIANTS),
    )
    return FIRST_TOUCH_VARIANTS[idx]


def pick_follow_up_variant(
    *,
    channel: str,
    prospect_id: int | str | None = None,
    campaign_id: int | str | None = None,
    step_day: int = 0,
) -> FollowUpVariant:
    # Semilla distinta al primer toque para no repetir siempre el mismo "estilo".
    idx = _stable_index(
        seed=_seed_parts(
            prospect_id=prospect_id,
            channel=channel,
            campaign_id=campaign_id,
            extra=f"follow|{int(step_day or 0)}",
        ),
        n=len(FOLLOW_UP_VARIANTS),
    )
    return FOLLOW_UP_VARIANTS[idx]


def _norm_channel(channel: str) -> Channel:
    ch = (channel or "email").strip().lower()
    if ch in ("linkedin", "li"):
        return "linkedin"
    if ch in ("whatsapp", "wa"):
        return "whatsapp"
    return "email"


def first_touch_structure_prompt(
    *,
    channel: str,
    variant: FirstTouchVariant,
    sender_name: str,
    brand_name: str,
    prospect_first_name: str,
    prospect_role: str = "",
    prospect_company: str = "",
) -> str:
    """Instrucciones de estructura para el LLM (bloques grandes, no slots chicos)."""
    ch = _norm_channel(channel)
    sender = (sender_name or "").strip() or "[nombre SDR]"
    brand = (brand_name or "").strip() or "[empresa]"
    first = (prospect_first_name or "").strip() or "[Nombre]"
    role = (prospect_role or "").strip() or "su rol"
    company = (prospect_company or "").strip() or "su empresa"
    label = _FIRST_LABELS[variant]

    common = f"""VARIANTE AUTOMÁTICA DE PRIMER TOQUE: {label}
Remitente: {sender} · Marca: {brand} · Prospecto: {first} ({role} · {company}).

REGLA CRÍTICA — BLOQUES GRANDES:
- NO uses plantillas rígidas con un hueco chico (PROHIBIDO: «Te escribo porque ayudamos a equipos comerciales a …»).
- Cada renglón de valor es un BLOQUE editable amplio: audiencia real, problema, producto y beneficio salen del PRODUCTO / ICP / research / rol-empresa del prospecto.
- No asumas «equipos comerciales» ni ninguna audiencia fija: usá la del producto/campaña/prospecto.
- VALOR = reescribí la ficha del producto en tus palabras (voz «nosotros…»), NUNCA copies
  value_proposition ni description literal ni casi literal. Sacá hechos concretos
  (%, canales, resultado medible) y redactalos conversacionalmente.
  PROHIBIDO conjugación rota («Con {{producto}} incrementa/automatiza…» → usá «incrementamos/automatizamos»).
  PROHIBIDO buzzwords vacíos («menos fricción operativa», «mejorar la eficiencia», «optimizar procesos»).
  PROHIBIDO que todos los mensajes repitan la misma explicación pegada de la ficha.
- Objetivo: agendar reunión breve. NUNCA cerrar la venta.
- sections.greeting: "Hola {first}," (o equivalente natural del canal).
- Firma email (si aplica): Saludos, {sender}
"""

    shape = f"""
ESTRUCTURA NEXUS (default — no la cambies salvo plantilla propia del SDR en Mi estilo):
sections.presentation: "Soy {sender} de {brand}."
sections.problem: vacío.
sections.solution: PUENTE obligatorio — «Vi que sos {role} de {company}, y …» + valor del producto
  en la MISMA idea (no ancla sola + párrafo «Con {{producto}}…»). Voz nosotros; hechos de la ficha.
  Email 1–2 oraciones; LinkedIn 1; WhatsApp 1 corta.
sections.benefits: vacío.
sections.cta: UNA pregunta para agendar reunión/llamada breve (?).
body = greeting + presentation + solution (ancla+valor puentados) + cta (+ firma email).
PROHIBIDO: corte seco ancla / valor; «No te escribo solo»; buzzwords vacíos.
Variante interna (solo CTA): {label}.
"""

    if ch == "email":
        length = (
            "Email: ideal 70-110 palabras (55-120 ok). SUBJECT breve, curiosidad, "
            "personalizado a la empresa del prospecto — sin spam.\n"
        )
    elif ch == "linkedin":
        length = (
            "LinkedIn: largo INTERMEDIO — más corto que email, más desarrollado que WhatsApp. "
            "Ideal ~260-420 caracteres (máx ~480). Sin subject.\n"
            "OBLIGATORIO: (1) «¿cómo estás?» o «¿cómo va?» en el renglón del saludo, tras una coma "
            "(«Hola Jorge, ¿cómo estás?»), no después de presentarte; "
            "(2) UNA línea de contexto humano sobre empresa/rol del prospecto "
            "(variada: «Vi [Empresa] y tu perfil como [rol]», «Te escribo por tu perfil como [rol] en [Empresa]» "
            "— NO repetir siempre «Te escribo por tu rol» ni «me pareció oportuno… por tu lugar como»; "
            "NO copies el headline de LinkedIn con la empresa pegada al cargo); "
            "(3) UNA oración de valor (producto una sola vez); (4) CTA reunión.\n"
            "PROHIBIDO nombrar la empresa del prospecto más de UNA vez.\n"
            "PROHIBIDO nombrar el producto más de UNA vez.\n"
            "Si ya dijiste que escribiste por mail/WhatsApp, NO vuelvas a nombrar el producto en esa frase.\n"
            "Presentación: «Soy {sender} de {brand}.» — no «Te contacto desde {brand}. Soy {sender}.»\n"
            "OBLIGATORIO párrafos cortos separados por línea en blanco.\n"
            "PROHIBIDO pegar o parafrasear casi literal la ficha Producto/servicio "
            "(value_proposition + description enteras). "
            "Reescribí valor conversacional. "
            "Si la ficha empieza con «Automatiza…», NO lo copies: "
            "decí p.ej. «Con {producto} automatizamos…» en una frase corta.\n"
        )
        length = length.replace("{sender}", sender).replace("{brand}", brand)
    else:
        length = (
            "WhatsApp: MÁS CORTO que LinkedIn. Informal, chill, según el país de Mi estilo. "
            "Ideal 20-35 palabras (máx ~45 / ~260 caracteres).\n"
            "OBLIGATORIO 2–3 micro-párrafos (línea en blanco): saludo; 1 idea de valor; CTA. "
            "PROHIBIDO párrafo muro y PROHIBIDO pegar la ficha de producto.\n"
        )

    return common + shape + length


def follow_up_structure_prompt(
    *,
    channel: str,
    variant: FollowUpVariant,
    step_day: int = 0,
    value_depth: str = "chill",
) -> str:
    """Follow-up outbound (sin respuesta): dosificación de valor + CTA; cierre «Quedo atento» con cupo."""
    ch = _norm_channel(channel)
    label = _FOLLOW_LABELS[variant]
    day = f"Día {step_day}" if step_day else "seguimiento"
    depth = (value_depth or "chill").strip().lower()
    if depth == "minimal":
        value_rule = (
            "DOSIFICACIÓN VALOR = minimal (FU tardío): CASI NO expliques el producto. "
            "Retomá el hilo + CTA. PROHIBIDO re-pegar la propuesta de valor del Día 1."
        )
    else:
        value_rule = (
            "DOSIFICACIÓN VALOR = chill (FU temprano): como mucho UNA idea corta/ángulo nuevo. "
            "PROHIBIDO re-pitchear el producto completo del Día 1."
        )

    base = f"""VARIANTE AUTOMÁTICA DE FOLLOW-UP ({day}): {label}
MODO FOLLOW-UP (sin respuesta previa):
- {value_rule}
- PROHIBIDO ABSOLUTO re-anclar: no digas «Vi que sos {{rol}} de/en {{empresa}}»,
  «Vi tu lugar como…», «Me cruzé con tu perfil…» ni equivalentes.
  Apalancate del hilo anterior (mail/LI/WA previos). El rol/empresa ya se dijeron.
- NO culpar («¿revisaste mi mensaje?», «sin respuesta»).
- Un solo CTA de reunión (nosotros ofrecemos). No hace falta «Quedo atento» en todos.
- «Quedo atento» máx. 2 veces en TODA la secuencia; si ya se usó ese cupo, no lo repitas.
- PROHIBIDO medir interés residual: «¿seguís interesad@?», «¿seguís con ganas de verlo?»,
  «¿todavía te interesa?» y equivalentes. Ofrecé reunión; no preguntes si sigue interesado.
- Si sumás un dato de valor, que sea NUEVO (no dicho antes con esta persona) y una sola vez.
  PROHIBIDO inventar hechos fuera de la ficha del producto.
- NO martilles el nombre del producto (máx. 1× por mensaje; en FU si ya se nombró, preferí 0).
"""

    if ch == "email" and variant == "soft_summary":
        return (
            base
            + """
EMAIL · EF1 — Resumen corto + puerta abierta:
1–2 oraciones que retoman el hilo (ángulo de valor breve, editable; sin plantilla fija).
CTA: coordinar reunión breve (una sola pregunta).
50-90 palabras. Preferí mismo hilo (Re:).
"""
        )
    if ch == "email":
        return (
            base
            + """
EMAIL · EF2 — Pregunta suave + disponibilidad:
1 oración de contexto («te había escrito por…» — libre, sin audiencia fija).
CTA: ¿tiene sentido una llamada corta en los próximos días?
50-90 palabras. Preferí mismo hilo (Re:).
"""
        )
    if ch == "linkedin" and variant == "soft_summary":
        return (
            base
            + """
LINKEDIN · LF1 — Cierre en una idea:
1–2 oraciones en 2 párrafos cortos: recordatorio breve + (si aplica) valor en una línea.
CTA: charla corta / 10 min.
180-400 caracteres. Más espacio que WhatsApp; sin muro de texto.
"""
        )
    if ch == "linkedin":
        return (
            base
            + """
LINKEDIN · LF2 — Invitación sin presión (NO binaria agresiva):
1 oración anclada al HILO previo (párrafo 1) — NO «Vi que sos…».
CTA: cuando le venga bien, 10–15 min (párrafo 2).
180-380 caracteres.
"""
        )
    if ch == "whatsapp" and variant == "soft_summary":
        return (
            base
            + """
WHATSAPP · WF1 — Último toque corto y chill:
«te escribo por última vez sobre esto» (o equivalente natural).
1 idea de valor/contexto editable solo si aporta.
CTA: agendar unos minutos.
2 micro-párrafos (~15-35 palabras). MÁS corto que LinkedIn.
"""
        )
    return (
        base
        + """
WHATSAPP · WF2 — Directo, liviano y chill:
CTA corto: ¿te calza una charla corta esta semana?
Opcional: media oración de contexto solo si aporta.
Máxima brevedad (2 micro-párrafos).
"""
    )


# ---------------------------------------------------------------------------
# Deterministic CRM / OpenAI-fallback bodies (misma familia de variantes)
# ---------------------------------------------------------------------------


def _vp_clause(product: dict[str, Any] | None) -> str:
    name = ((product or {}).get("name") or "nuestra solución").strip() or "nuestra solución"
    return _rewrite_product_value(product, product_name=name, channel="email")


def _clip_sentence(text: str, *, max_chars: int) -> str:
    """Recorte seguro: no cortar en coma ni dejar colas a medias (todos los canales)."""
    from app.services.linkedin_reply_compose import (
        _clip_complete_thought,
        _strip_dangling_tails,
        utterance_looks_truncated,
    )

    t = re.sub(r"\s+", " ", (text or "").strip()).rstrip(" .,;")
    if not t:
        return t
    if len(t) <= max_chars and not utterance_looks_truncated(t):
        return t
    # Misma política que réplicas: pensamiento completo > cupo estricto.
    out = _clip_complete_thought(t if t.endswith((".", "?", "!")) else t + ".", max_chars)
    out = out.rstrip()
    if utterance_looks_truncated(out):
        out = _strip_dangling_tails(out.rstrip(".!? ")) + "."
    return out.rstrip(" .,;")


def _strip_leading_product_name(text: str, product_name: str) -> str:
    from app.services.cold_message_bank import strip_filler_phrases

    t = strip_filler_phrases((text or "").strip())
    name = (product_name or "").strip()
    if name:
        t = re.sub(rf"^(?:con|desde|a\s+trav[eé]s\s+de)\s+{re.escape(name)}\s*,?\s*", "", t, flags=re.I)
    if name and t.lower().startswith(name.lower()):
        t = t[len(name) :].lstrip(" ,.-:")
    return t


# Frases de ficha que no deben llegar al mensaje (buzzwords / marketing interno).
# NO borrar «IA entrenada…»: es valor concreto del producto y al cortarlo queda
# conjugación rota («con entrenada»).
_GENERIC_FLUFF_RE = re.compile(
    r"\b(?:"
    r"reduc(?:iendo|e|ir)?\s+la\s+fricci[oó]n\s+operativa|"
    r"menos\s+fricci[oó]n\s+operativa|"
    r"mejorar(?:\s+la)?\s+eficiencia|"
    r"optimizar\s+(?:los\s+)?procesos|"
    r"automatizaci[oó]n\s+asistida|"
    r"control\s+humano|"
    r"plataforma\s+multicanal\s+de\s+outreach\s+comercial"
    r")\b[,.]?",
    re.I,
)

_BROKEN_VALUE_FRAGMENT_RE = re.compile(
    r"\b(?:con\s+entrenada|una\s+entrenada|por\s+entrenada)\b",
    re.I,
)

_THIRD_PERSON_START = re.compile(
    r"^(automatiza|orquesta|centraliza|integra|consolida|permite|reduce|"
    r"incrementa|aumenta|acelera|simplifica|facilita|genera|conecta|"
    r"organiza|gestiona|ofrece|ayuda|mejora|optimiza|elimina|ahorra|"
    r"multiplica|potencia|impulsa|conserva|evita|protege|habilita|"
    r"asigna|incluye|cubre)\b",
    re.I,
)

_NOSOTROS_VERB = {
    "automatiza": "automatizamos",
    "orquesta": "orquestamos",
    "centraliza": "centralizamos",
    "integra": "integramos",
    "consolida": "consolidamos",
    "permite": "permitimos",
    "reduce": "reducimos",
    "incrementa": "incrementamos",
    "aumenta": "aumentamos",
    "acelera": "aceleramos",
    "simplifica": "simplificamos",
    "facilita": "facilitamos",
    "genera": "generamos",
    "conecta": "conectamos",
    "organiza": "organizamos",
    "gestiona": "gestionamos",
    "ofrece": "ofrecemos",
    "ayuda": "ayudamos",
    "mejora": "mejoramos",
    "optimiza": "optimizamos",
    "elimina": "eliminamos",
    "ahorra": "ahorramos",
    "multiplica": "multiplicamos",
    "potencia": "potenciamos",
    "impulsa": "impulsamos",
    "conserva": "conservamos",
    "evita": "evitamos",
    "protege": "protegemos",
    "habilita": "habilitamos",
    "asigna": "asignamos",
    "incluye": "incluimos",
    "cubre": "cubrimos",
}


def _scrub_marketing_fluff(text: str) -> str:
    t = re.sub(r"\bmensajes\s+personalizados\s+generados\s+por\s+IA\b", "IA", text or "", flags=re.I)
    t = _GENERIC_FLUFF_RE.sub(" ", t)
    t = re.sub(r"\b(con|una|por)\s+entrenada\b", r"\1 IA entrenada", t, flags=re.I)
    t = re.sub(r"\s*,\s*,+", ", ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",\s*\.", ".", t)
    return t.strip(" ,.;")


def _has_broken_value_fragment(text: str | None) -> bool:
    return bool(_BROKEN_VALUE_FRAGMENT_RE.search(text or ""))


def _product_ficha_blob(product: dict[str, Any] | None) -> str:
    return " ".join(
        str((product or {}).get(k) or "")
        for k in ("name", "description", "value_proposition")
    )


def _ficha_claims_outbound_automation(product: dict[str, Any] | None) -> bool:
    """True solo si ESTA ficha de Producto habla de prospección/outbound/multicanal."""
    ficha = _product_ficha_blob(product).lower()
    if not ficha.strip():
        return False
    return bool(
        re.search(
            r"prospecci|outbound|multicanal|reuniones\s+comerciales|"
            r"secuencia(?:s)?\s+(?:de\s+)?(?:contacto|outreach)|"
            r"tareas\s+manuales\s+de\s+prospecci|"
            r"linkedin.{0,40}whatsapp|whatsapp.{0,40}linkedin",
            ficha,
        )
    )


def _is_outbound_saas_product(product: dict[str, Any] | None) -> bool:
    """Compat: claims de outreach solo si están en la ficha del Producto."""
    return _ficha_claims_outbound_automation(product)


def _complete_within_limit(text: str, *, max_chars: int) -> str:
    """
    Recorte que prioriza idea completa: si no entra, corta en frontera natural
    (frente a / o depender / …) en vez de dejar «exclusivamente.».
    """
    from app.services.linkedin_reply_compose import utterance_looks_truncated

    t = re.sub(r"\s+", " ", (text or "").strip()).rstrip(" .,;")
    if not t:
        return t
    if len(t) <= max_chars and not utterance_looks_truncated(t):
        return t
    for pat in (
        r"\s+frente\s+a\b.*$",
        r"\s+en\s+lugar\s+de\b.*$",
        r"\s+en\s+vez\s+de\b.*$",
        r"\s+o\s+depender\b.*$",
        r"\s+o\s+mantener\b.*$",
        r",\s+conservando\b.*$",
        r",\s+sin\s+tener\s+que\b.*$",
    ):
        shortened = re.sub(pat, "", t, flags=re.I).rstrip(" .,;")
        if (
            len(shortened) >= 36
            and len(shortened) <= max_chars
            and not utterance_looks_truncated(shortened)
        ):
            return shortened
    out = _clip_sentence(t, max_chars=max_chars)
    if utterance_looks_truncated(out):
        from app.services.linkedin_reply_compose import _strip_dangling_tails

        repaired = _strip_dangling_tails(out.rstrip(".!? ")).rstrip(" .,;")
        if len(repaired) >= 36 and not utterance_looks_truncated(repaired):
            return repaired
        return ""
    return out


def _markdown_bullet_benefits(raw: str) -> list[str]:
    """Extrae bullets tipo «**Título:** cuerpo» para variar el ángulo."""
    text = raw or ""
    out: list[str] = []
    for m in re.finditer(
        r"(?m)^\s*[-*•]\s+\*\*([^*]+)\*\*:?\s*(.+?)(?=\n\s*[-*•]\s+\*\*|\n\n|\Z)",
        text,
        re.S,
    ):
        title = re.sub(r"\s+", " ", m.group(1)).strip(" .:;")
        body = re.sub(r"\s+", " ", m.group(2)).strip(" .:;")
        body = re.sub(r"[*`#_]+", "", body)
        if title and len(title) >= 12:
            out.append(title)
        if body and len(body) >= 40:
            # Primera oración del bullet — solo si entra corta (LinkedIn).
            first = re.split(r"(?<=[.!?])\s+", body)[0].strip(" .:;")
            if first and len(first) <= 110:
                out.append(first)
    return out


def _short_product_benefits(text: str, *, max_chars: int = 140) -> list[str]:
    """Ideas usables de una ficha (varias, para no repetir el mismo pitch)."""
    raw = text or ""
    bullets = _markdown_bullet_benefits(raw)
    flat = re.sub(r"[*`#_]+", " ", raw)
    flat = re.sub(r"\s+", " ", flat).strip(" .;")
    chunks = re.split(r"(?<=[.!?])\s+|\s*[•*\-]\s+", flat) if flat else []
    usable: list[str] = []
    for chunk in [*bullets, *chunks]:
        c = chunk.strip(" .;")
        if len(c) < 24:
            continue
        if re.match(r"^(las\s+principales|en\s+resumen|resumo)\b", c, re.I):
            continue
        clipped = _complete_within_limit(c, max_chars=max_chars)
        if clipped and clipped not in usable:
            usable.append(clipped)
    return usable


def _short_product_benefit(text: str, *, max_chars: int = 140) -> str:
    """Primera idea usable de una ficha larga (sin pegar brochure completo)."""
    opts = _short_product_benefits(text, max_chars=max_chars)
    return opts[0] if opts else ""


def _value_line_is_broken(text: str) -> bool:
    """True si el cliente leería una frase rota, sin sentido o sin oferta clara."""
    from app.services.linkedin_reply_compose import utterance_looks_truncated

    t = (text or "").strip()
    if not t or utterance_looks_truncated(t):
        return True
    low = t.lower()
    # Artefacto de clip/corrupción: «Uber Direct e. comercio…» / letra suelta + punto.
    if re.search(r"(?<![0-9])\b[a-záéíóúñ]\.\s+[a-záéíóúñ]{3,}\b", low):
        return True
    if re.search(r"\b(?:con|desde)\s+[^.!?\n]{2,48}\s+[a-záéíóúñ]\.\s+\w+", low):
        return True
    # «Con X, la plataforma asigna…» (sujeto distinto pegado) — no «Con X, el comercio…».
    if re.search(
        r"\b(?:con|desde)\s+[^,\n]{2,40},\s+"
        r"(?:la\s+plataforma|el\s+sistema|la\s+app|la\s+herramienta)\s+"
        r"(?:asigna|permite|ofrece|conserva|evita|incluye)\b",
        low,
    ):
        return True
    if re.search(
        r"\bofrecemos\s+(?:la|el|los|las)\s+\w+\s+"
        r"(?:asigna|permite|ofrece|conserva|evita|incluye)\b",
        low,
    ):
        return True
    # Feature suelta: el prospecto no entiende qué le ofrecen.
    if re.search(
        r"\b(?:aportamos|sumamos)\s+[\wáéíóúñ\s]{0,48}\s+seg[uú]n\s+el\s+tipo\b",
        low,
    ):
        if not re.search(r"para|entreg|env[ií]o|canal|comercio|marketplace", low):
            return True
    if re.search(r"\basignamos\s+(?:el\s+)?veh[ií]culo\s+seg[uú]n\b", low):
        if not re.search(
            r"canal|marketplace|comercio|propio|sin\s+depender|marca\s+blanca|flota",
            low,
        ):
            return True
    if len(t) < 52:
        return True
    # «resolvemos herramientas de…» / feature suelta sin oferta.
    if re.search(r"\bresolvemos\s+(?:herramientas|protecci|variedad|seguimiento)\b", low):
        return True
    return False


def _value_line_has_clear_offer(text: str) -> bool:
    """El prospecto debería entender qué le ofrecen (no un feature aislado)."""
    low = (text or "").lower()
    # Delivery / Uber Direct
    if re.search(
        r"canal(?:es)?\s+(?:propio|de\s+venta|del\s+comercio)|"
        r"marketplace|flota\s+propia|flota\s+de\s+uber|"
        r"margen|comisi[oó]n|despach|"
        r"entregas?\s+locales|sin\s+que\s+el\s+comercio|"
        r"repartidores?\s+propios|desde\s+(?:sus|tus|los)\s+propios\s+canales|"
        r"red\s+de\s+repartidores",
        low,
    ):
        return True
    # Otros productos: verbo de valor + beneficio concreto (stock, reuniones, etc.).
    return bool(
        re.search(
            r"\b(?:ayudamos|reducimos|automatizamos|permitimos|damos|evitamos|"
            r"centralizamos|operamos|habilitamos|orquestamos|bajamos)\b",
            low,
        )
        and re.search(
            r"reuniones|quiebres|stock|inventario|automatiz|outbound|prospecci|"
            r"cobran|factur|ahorr|costo|pedido|alerta|seguimiento|manual",
            low,
        )
    )


def _value_line_leads_with_payment(text: str) -> bool:
    """Primer mensaje: no abrir con precio/pago (aunque esté en la ficha)."""
    low = (text or "").lower()
    return bool(
        re.search(
            r"\b(?:se\s+paga|pagar|pagás|paga\s+por|pagando|"
            r"tarifa\s+fija|precio|cu[aá]nto\s+cuesta)\b",
            low,
        )
    )


def _strip_product_value_prefix(text: str, product_name: str) -> str:
    """Saca aperturas variadas + «Con/Desde {producto}» para comparar el núcleo."""
    name = (product_name or "").strip()
    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(
        r"^(?:te\s+resumo|en\s+la\s+pr[aá]ctica|hoy|la\s+idea\s+es\s+simple|"
        r"la\s+propuesta(?:\s+con\s+[^.:,]+)?|lo\s+que\s+buscamos(?:\s+con\s+[^.:,]+)?)"
        r"\s*[:,]?\s*",
        "",
        t,
        flags=re.I,
    ).strip()
    if name:
        t = re.sub(
            rf"^(?:nosotros\s+)?(?:con|desde)\s+{re.escape(name)}\s*,?\s*",
            "",
            t,
            flags=re.I,
        )
    t = re.sub(r"^(?:nosotros\s+)?", "", t, flags=re.I).strip()
    if t and t[0].isupper():
        t = t[0].lower() + t[1:]
    return t.strip(" .,;")


def connect_prospect_anchor_to_value(
    anchor: str,
    value: str,
    *,
    seed: str = "",
    separate_paragraphs: bool = True,
) -> str:
    """
    Une ancla CRM («Vi que sos…») con el valor del producto.
    A veces puente natural; si no encaja, dos frases limpias. Nunca empalme roto.
    """
    a = re.sub(r"\s+", " ", (anchor or "").strip())
    v = re.sub(r"\s+", " ", (value or "").strip())
    if not a:
        return v
    if not v:
        return a

    def _separate() -> str:
        if separate_paragraphs:
            return f"{a}\n\n{v}"
        return f"{a} {v}"

    # Solo puentear si el valor abre de forma continuable.
    if not re.match(
        r"(?i)^(con|desde|te\s+resumo|en\s+la\s+pr[aá]ctica|hoy|la\s+idea|"
        r"lo\s+que\s+buscamos|la\s+propuesta)\b",
        v,
    ):
        return _separate()

    # Si el valor es continuable (con/desde…), SIEMPRE puentear (regla 3b).
    # Variar solo la forma del puente — no volver al corte seco ancla / valor.
    mode = 0
    if seed:
        head = (seed.split("|", 1)[0] or "").strip()
        if head.isdigit():
            mode = int(head) % 2
        else:
            digest = 0
            for ch_i in seed.encode("utf-8"):
                digest = (digest * 31 + ch_i) & 0xFFFFFFFF
            mode = digest % 2

    a_core = a.rstrip(".!? ").strip()
    # Solo muletillas que no se comen «con/desde {producto}…».
    v_core = re.sub(
        r"^(?:te\s+resumo|en\s+la\s+pr[aá]ctica|hoy|la\s+idea\s+es\s+simple)\s*[:,]?\s*",
        "",
        v,
        flags=re.I,
    ).strip()
    if not v_core:
        return _separate()
    # Tras limpiar, tiene que seguir abriendo con con/desde (si no, no puentear).
    if not re.match(r"(?i)^(con|desde)\b", v_core):
        return _separate()
    if v_core[0].isupper():
        v_core = v_core[0].lower() + v_core[1:]
    if not v_core.endswith((".", "?", "!")):
        v_core += "."

    if mode == 0:
        # «Vi que sos X en Y, y con Producto…»
        return f"{a_core}, y {v_core}"
    return f"{a_core}. Para un rol como el tuyo, {v_core}"


def _vary_value_opener(line: str, *, product_name: str, seed: str = "") -> str:
    """
    Misma idea, aperturas distintas — no siempre «Nosotros con {producto}…».
    """
    name = (product_name or "nuestra solución").strip() or "nuestra solución"
    core = _strip_product_value_prefix(line, name)
    if not core:
        return (line or "").strip()
    # Si el core ya nombra el producto, no duplicar.
    if re.search(rf"\b{re.escape(name)}\b", core, re.I):
        wrappers = (
            core[0].upper() + core[1:],
            f"En la práctica, {core}",
            f"Te resumo: {core}",
            f"La idea es simple: {core}",
        )
    elif re.match(
        r"^(?:el|la|los|las)\s+\w+",
        core,
        re.I,
    ):
        # «el comercio despacha…» — aperturas naturales.
        wrappers = (
            f"Con {name} {core}",
            f"Desde {name} {core}",
            f"En la práctica, con {name} {core}",
            f"Te resumo: con {name} {core}",
            f"Lo que buscamos con {name} es que {core}",
            f"Hoy, con {name}, {core}",
        )
    else:
        # Core ya en 1ª plural («permitimos / damos / evitamos…»).
        wrappers = (
            f"Con {name} {core}",
            f"Desde {name} {core}",
            f"En la práctica, con {name} {core}",
            f"Te resumo: con {name} {core}",
            f"La propuesta con {name}: {core}",
            f"Hoy, con {name}, {core}",
        )
    idx = 0
    if seed and len(wrappers) > 1:
        head = (seed.split("|", 1)[0] or "").strip()
        if head.isdigit():
            idx = int(head) % len(wrappers)
        else:
            digest = 0
            for ch_i in seed.encode("utf-8"):
                digest = (digest * 31 + ch_i) & 0xFFFFFFFF
            idx = digest % len(wrappers)
    out = wrappers[idx].strip()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    if not out.endswith((".", "?", "!")):
        out += "."
    return out


def _non_saas_value_lines(
    product: dict[str, Any] | None,
    *,
    product_name: str,
    max_chars: int,
) -> list[str]:
    """
    Oraciones de valor legibles: qué hacemos + el beneficio (ficha del Producto).
    Nunca pitch de otro cliente ni frases pegadas sin sentido.
    """
    name = (product_name or "nuestra solución").strip() or "nuestra solución"
    vp = str((product or {}).get("value_proposition") or "").strip()
    desc = str((product or {}).get("description") or "").strip()

    out: list[str] = []
    seen: set[str] = set()

    def _add(line: str) -> None:
        # LinkedIn: una idea completa; cupo un poco más holgado que el hard clip viejo.
        blurb = _complete_within_limit(line.strip(), max_chars=max(max_chars, 210))
        if not blurb or _value_line_is_broken(blurb):
            return
        # Primer toque: no guardar variantes de “se paga / tarifa”.
        if _value_line_leads_with_payment(blurb):
            return
        key = blurb.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(blurb)

    # 1) Description accionable («Permite utilizar…») + paráfrasis del mismo valor.
    if desc:
        flat_desc = _scrub_marketing_fluff(
            _strip_leading_product_name(re.sub(r"\s+", " ", desc), name)
        )
        flat_desc = re.sub(r"[*`#_]+", "", flat_desc).strip()
        own_channel = bool(
            re.search(r"propios\s+canales|canal(?:es)?\s+de\s+venta|marca\s+blanca", flat_desc, re.I)
        )
        # Saltar definición «es una solución…»; buscar verbo de acción.
        for chunk in re.split(r"(?<=[.!?])\s+", flat_desc):
            c = chunk.strip(" .;")
            if len(c) < 36:
                continue
            if re.match(r"^(?:es|esta|ésta)\b", c, re.I):
                continue
            m2 = _THIRD_PERSON_START.match(c)
            if m2:
                verb = _to_nosotros_verb(m2.group(1))
                rest = c[m2.end() :].strip(" ,")
                rest = _complete_within_limit(rest, max_chars=max(90, max_chars - 30))
                if rest:
                    _add(f"Con {name} {verb} {rest}")
                    if len(rest) < 110 and re.search(
                        r"entreg|repartidor|flota|canal", rest, re.I
                    ):
                        _add(
                            f"Con {name} {verb} {rest}, "
                            "sin que el comercio arme flota propia"
                        )
            elif re.match(r"^(?:permitir|utilizar|usar|habilitar|operar)\b", c, re.I):
                low = c[0].lower() + c[1:]
                _add(f"Con {name} podemos {low}")
        # Misma oferta (canales propios + red Uber), palabras distintas.
        if own_channel and ("uber" in flat_desc.lower() or "uber" in name.lower()):
            _add(
                f"Con {name} el comercio despacha desde su web, app o WhatsApp "
                "y la entrega la hace la red de Uber, sin publicar el local en Uber Eats"
            )
            _add(
                f"Con {name} habilitamos entregas locales el mismo día "
                "con repartidores de Uber, pero vendiendo desde el canal propio del negocio"
            )
            _add(
                f"Con {name} el cliente compra en la tienda del comercio "
                "y un socio repartidor de Uber retira y entrega el pedido"
            )

    ficha_l = _product_ficha_blob(product).lower()
    fleet_bit = ""
    if "uber" in ficha_l or "uber" in name.lower():
        fleet_bit = "con la flota de Uber"
    elif re.search(r"repartidor|flota|delivery|entrega", ficha_l):
        fleet_bit = "con la red de repartidores"

    # 2) Bullets VP: título = ángulo, cuerpo = claim conjugable.
    for m in re.finditer(
        r"(?m)^\s*[-*•]\s+\*\*([^*]+)\*\*:?\s*(.+?)(?=\n\s*[-*•]\s+\*\*|\n\n|\Z)",
        vp,
        re.S,
    ):
        title = re.sub(r"\s+", " ", m.group(1)).strip(" .:;")
        body = re.sub(r"\s+", " ", re.sub(r"[*`#_]+", "", m.group(2))).strip(" .:;")
        first = re.split(r"(?<=[.!?])\s+", body)[0].strip(" .:;") if body else ""
        m_body = _THIRD_PERSON_START.match(first) if first else None
        if m_body:
            verb = _to_nosotros_verb(m_body.group(1))
            rest = first[m_body.end() :].strip(" ,")
            rest = _complete_within_limit(rest, max_chars=120)
            if rest:
                _add(f"Con {name} {verb} {rest}")
        # Título → oración de valor completa (qué hacemos + beneficio).
        title_l = title[0].lower() + title[1:] if title else ""
        if title_l and 12 <= len(title) <= 70:
            if re.search(r"cobertura|l[ií]quidez", title_l, re.I):
                extra = f" {fleet_bit}" if fleet_bit else ""
                _add(
                    f"Con {name} damos cobertura urbana inmediata{extra}, "
                    "sin que el comercio arme flota propia"
                )
            elif re.search(r"veh[ií]culo|carga|bulto", title_l, re.I):
                # Oferta clara (no un feature suelto de “asignamos el vehículo”).
                _add(
                    f"Con {name} el comercio despacha desde su propio canal "
                    "y asignamos bici, moto o auto según la carga, "
                    "sin meter el local en un marketplace"
                )
            elif re.search(r"m[aá]rgen|comisi", title_l, re.I):
                _add(
                    f"Con {name} el comercio paga por distancia y conserva el margen "
                    "de sus productos, sin comisión tipo marketplace"
                )
            elif re.search(r"ahorro|costo|fijo|personal", title_l, re.I):
                # Primer toque: valor operativo completo, no “se paga…” ni scrap corto.
                _add(
                    f"Con {name} el comercio opera entregas sin cargar costos fijos "
                    "de repartidores propios ni tiempos muertos de flota interna, "
                    "con la red bajo demanda"
                )
            # Sin molde claro: no inventar «resolvemos {título}» (suena a feature suelta).

    # 3) VP intro sin bullets (fallback).
    if vp and not out:
        flat_vp = _scrub_marketing_fluff(
            _strip_leading_product_name(re.sub(r"\s+", " ", re.sub(r"[*`#_]+", " ", vp)), name)
        )
        m = re.match(
            r"^((?:operar|permitir|usar|utilizar|habilitar|conectar)[^.]{8,120}?)\s+"
            r"ofrece(?:n)?\s+(.+)$",
            flat_vp,
            re.I,
        )
        if m:
            right = re.sub(
                r"^ventajas\s+espec[ií]ficas\s+enfocadas\s+en\s+",
                "",
                m.group(2).strip(" .,;"),
                flags=re.I,
            )
            right = _complete_within_limit(right, max_chars=130)
            if right:
                low_r = right[0].lower() + right[1:]
                _add(
                    f"Con {name} operamos con la flota de Uber para darles {low_r}"
                )
        for chunk in _short_product_benefits(flat_vp, max_chars=140)[:4]:
            m2 = _THIRD_PERSON_START.match(chunk)
            if m2:
                verb = _to_nosotros_verb(m2.group(1))
                rest = chunk[m2.end() :].strip(" ,")
                if rest:
                    _add(f"Con {name} {verb} {rest}")
            elif re.match(r"^[a-záéíóúñ]+(?:amos|emos|imos)\b", chunk, re.I):
                # VP ya en 1ª plural («Reducimos quiebres…»).
                rest = chunk[0].lower() + chunk[1:] if chunk else chunk
                _add(f"Con {name} {rest}")

    # Filtrar otra pasada por si quedó algo raro.
    return [line for line in out if not _value_line_is_broken(line)]


def _safe_linkedin_value_blurb(
    product: dict[str, Any] | None,
    *,
    product_name: str,
    seed: str = "",
) -> str:
    """
    Valor LinkedIn = solo lo que diga el Producto de esa campaña.
    Nunca inventa pitch de Nexus ni de otro cliente.
    Apertura variada (no siempre «Nosotros con…»).
    """
    name = (product_name or "nuestra solución").strip() or "nuestra solución"
    raw = _rewrite_product_value(product, product_name=name, channel="linkedin", seed=seed)
    return _vary_value_opener(raw, product_name=name, seed=seed or "0")


def _to_nosotros_verb(verb: str) -> str:
    key = (verb or "").lower()
    if key in _NOSOTROS_VERB:
        return _NOSOTROS_VERB[key]
    if key.endswith("a") and not key.endswith("amos"):
        return key[:-1] + "amos"
    if key.endswith("e") and not key.endswith(("emos", "imos")):
        return key[:-1] + "imos"
    return key


def _concrete_hooks(text: str) -> list[str]:
    """Saca ganchos concretos (%, canales, reuniones) sin pegar la ficha."""
    t = text or ""
    hooks: list[str] = []
    for m in re.finditer(r"\d{1,3}\s*%(?:\s*(?:a|y|-|–)\s*\d{1,3}\s*%)?", t, re.I):
        hooks.append(m.group(0).replace("–", "-"))
    low = t.lower()
    channels = [ch for ch in ("email", "linkedin", "whatsapp") if ch in low]
    if len(channels) >= 2:
        pretty = {"email": "email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp"}
        hooks.append(", ".join(pretty[c] for c in channels))
    if re.search(r"\breuni(ones|ón|on)\b", t, re.I):
        hooks.append("más reuniones")
    if re.search(r"\bprospecci[oó]n\b", t, re.I):
        hooks.append("prospección")
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for h in hooks:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def _rewrite_product_value(
    product: dict[str, Any] | None,
    *,
    product_name: str,
    channel: str = "email",
    seed: str = "",
    explain_more: bool = False,
) -> str:
    """
    Valor = solo lo que diga el Producto de esa campaña/usuario.
    No inventa Nexus ni claims de otro cliente. Si la ficha habla de
    prospección/outbound, se puede condensar eso; si no, se reescribe la ficha tal cual.
    """
    ch = _norm_channel(channel)
    # Valor completo: no ratonear por canal (variedad ≠ mensaje corto).
    max_chars = 240 if ch in ("email", "linkedin", "whatsapp") else 220
    if explain_more and ch == "email":
        max_chars = 320
    raw_vp = re.sub(r"\s+", " ", ((product or {}).get("value_proposition") or "").strip()).rstrip(".")
    raw_vp = _strip_leading_product_name(raw_vp, product_name)
    cleaned = _scrub_marketing_fluff(raw_vp)
    ficha_low = _product_ficha_blob(product).lower()

    hooks = _concrete_hooks(cleaned) or _concrete_hooks(raw_vp)
    # % solo cuenta si la ficha habla de automatizar/prospección (no comisiones ajenas).
    pct = ""
    if re.search(r"automatiz|prospecci|outbound|tareas\s+manuales", ficha_low):
        pct = next((h for h in hooks if "%" in h), "")
    channels = ""
    if re.search(r"linkedin|whatsapp|email|multicanal", ficha_low):
        channels = next(
            (h for h in hooks if "linkedin" in h.lower() or "whatsapp" in h.lower()),
            "",
        )
    grounded_outbound = _ficha_claims_outbound_automation(product)

    variants: list[str] = []
    # Siempre: líneas fieles a description + value_proposition de ESTE producto.
    variants.extend(
        _non_saas_value_lines(product, product_name=product_name, max_chars=max_chars)
    )

    # Extra condensado solo si ESTA ficha ya habla de outreach (no plantilla Nexus genérica).
    if grounded_outbound:
        if pct and channels:
            variants.append(
                f"Con {product_name} automatizamos entre {pct} del trabajo manual de "
                f"prospección y contacto por {channels}"
            )
            variants.append(
                f"Con {product_name} bajamos el trabajo manual de outbound "
                f"(cerca de {pct}) unificando {channels}"
            )
        elif pct and re.search(r"prospecci|outbound|tareas\s+manuales", ficha_low):
            variants.append(
                f"Con {product_name} automatizamos alrededor de {pct} de las tareas "
                "manuales de prospección outbound"
            )
        elif channels and re.search(r"prospecci|outbound|multicanal", ficha_low):
            variants.append(
                f"Con {product_name} centralizamos la prospección y el contacto por {channels} "
                "para agendar más reuniones"
            )
            variants.append(
                f"Con {product_name} orquestamos el outreach por {channels} "
                "sin perseguir cada lead a mano"
            )

        if re.search(r"\breuni", ficha_low):
            variants.append(
                f"Con {product_name} ayudamos a conseguir más reuniones comerciales "
                "con menos seguimiento manual"
            )

    full_vp = ""
    source = cleaned if (cleaned and len(cleaned) >= 18) else ""
    if source and grounded_outbound:
        m = _THIRD_PERSON_START.match(source)
        if m:
            verb = _to_nosotros_verb(m.group(1))
            rest = source[m.end() :].strip(" ,")
            if rest:
                full_vp = f"Con {product_name} {verb} {rest}"
                variants.append(full_vp)
        elif re.match(r"^[a-záéíóúñ]+(?:amos|emos|imos)\b", source, re.I):
            rest = source[0].lower() + source[1:] if source else source
            full_vp = f"Con {product_name} {rest}"
            variants.append(full_vp)

    # Dedup preservando orden
    deduped: list[str] = []
    seen_v: set[str] = set()
    for v in variants:
        key = v.lower().strip()
        if key and key not in seen_v:
            seen_v.add(key)
            deduped.append(v)
    variants = deduped

    if not variants:
        variants.append(
            f"Con {product_name} aportamos valor concreto según lo que necesitás"
        )

    # Pool usable: oferta clara, sin pago, sin frases rotas — cores distintos.
    from app.services.linkedin_reply_compose import utterance_looks_truncated

    prefer_no_pay = ch in ("linkedin", "whatsapp", "email")
    usable: list[str] = []
    seen_fp: set[str] = set()
    for raw_v in variants:
        cand = _complete_within_limit(raw_v, max_chars=max_chars) or raw_v
        if utterance_looks_truncated(cand) or _value_line_is_broken(cand):
            continue
        if not _value_line_has_clear_offer(cand):
            continue
        if prefer_no_pay and _value_line_leads_with_payment(cand):
            continue
        core = _strip_product_value_prefix(cand, product_name)
        # Variar sin achicar: el núcleo tiene que traer info suficiente.
        if len(core) < 80:
            continue
        fp = re.sub(r"[^a-záéíóúñ0-9]+", " ", core.lower())
        fp = re.sub(r"\s+", " ", fp).strip()[:96]
        if not fp or fp in seen_fp:
            continue
        seen_fp.add(fp)
        usable.append(cand)
    if not usable:
        # Fallback: las más largas entre las no rotas (mejor info que scrap).
        ranked = sorted(
            (
                _complete_within_limit(v, max_chars=max_chars) or v
                for v in variants
                if not _value_line_is_broken(v)
            ),
            key=lambda s: len(_strip_product_value_prefix(s, product_name)),
            reverse=True,
        )
        usable = ranked[: max(1, min(5, len(ranked)))] or variants[:]
    else:
        # Mantener varios núcleos largos (min core ya filtra flacos): variedad
        # sin colapsar a 2 frases calcadas entre prospectos cercanos.
        usable.sort(
            key=lambda s: len(_strip_product_value_prefix(s, product_name)),
            reverse=True,
        )
        keep = max(4, min(len(usable), (len(usable) + 1) // 2 + 2))
        usable = usable[:keep] if len(usable) > keep else usable

    idx = 0
    if seed and len(usable) > 1:
        head = (seed.split("|", 1)[0] or "").strip()
        if head.isdigit():
            idx = int(head) % len(usable)
        else:
            digest = 0
            for ch_i in seed.encode("utf-8"):
                digest = (digest * 31 + ch_i) & 0xFFFFFFFF
            idx = digest % len(usable)
    blurb = full_vp if (explain_more and ch == "email" and full_vp and full_vp in usable) else usable[idx]
    # Seguridad: nunca dejar 3ª persona pegada tras «Con {producto}».
    bad = re.search(
        rf"^Con\s+{re.escape(product_name)}\s+"
        r"(automatiza|incrementa|aumenta|centraliza|reduce|permite|integra)\b",
        blurb,
        re.I,
    )
    if bad:
        blurb = re.sub(
            rf"(Con\s+{re.escape(product_name)}\s+){bad.group(1)}\b",
            rf"\1{_to_nosotros_verb(bad.group(1))}",
            blurb,
            count=1,
            flags=re.I,
        )
    return _complete_within_limit(blurb, max_chars=max_chars) or blurb


def _how_clause(product: dict[str, Any] | None, *, product_name: str) -> str:
    """Una frase corta del mecanismo — nunca la ficha completa del producto."""
    return _rewrite_product_value(product, product_name=product_name, channel="linkedin")


def _outcome_clause(product: dict[str, Any] | None) -> str:
    name = ((product or {}).get("name") or "nuestra solución").strip() or "nuestra solución"
    return _rewrite_product_value(product, product_name=name, channel="email")


def _conversational_value_blurb(
    product: dict[str, Any] | None,
    *,
    product_name: str,
    channel: str,
    seed: str = "",
    explain_more: bool = False,
) -> str:
    """
    Valor corto para CRM/fallback: reescribe la ficha, no la pega.
    LinkedIn usa plantillas seguras (sin conjugaciones rotas).
    """
    if _norm_channel(channel) == "linkedin":
        return _safe_linkedin_value_blurb(
            product, product_name=product_name, seed=seed
        )
    return _rewrite_product_value(
        product,
        product_name=product_name,
        channel=channel,
        seed=seed,
        explain_more=explain_more,
    )


def _product_label(product: dict[str, Any] | None, *, brand: str) -> str:
    name = ((product or {}).get("name") or "").strip()
    if name:
        return name
    return brand or "nuestra solución"


def _cta_meeting() -> str:
    return "¿Te interesaría coordinar una reunión breve para ver si encaja?"


def build_first_touch_sections(
    *,
    channel: str,
    variant: FirstTouchVariant,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
) -> dict[str, str]:
    """Sections desde banco cold (callers legacy / CRM floor)."""
    from app.services.cold_message_bank import render_cold_bank_touch
    from app.services.outreach_display_names import (
        outreach_company_display,
        prospect_greeting_name,
        sender_first_name,
    )

    rendered = render_cold_bank_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        prior_touches=[],
        first_touch=True,
        step_day=1,
    )
    first = prospect_greeting_name(prospect)
    sender = sender_first_name(
        campaign_sender=campaign.get("sender_name"),
        fallback="el equipo",
    )
    brand = ""
    for key in ("brand_name", "company_name", "seller_company_name"):
        brand = outreach_company_display(campaign.get(key)) or ""
        if brand:
            break
    if not brand:
        brand = "nuestro equipo"
    company = (prospect.get("company_name") or "tu empresa").strip()
    role = (prospect.get("role") or prospect.get("selling_to_role") or "").strip()
    product_name = ((product or {}).get("name") or "").strip() or brand
    ch = _norm_channel(channel)
    body = rendered.body
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    greeting = lines[0] if lines else (f"Hola {first}," if first else "Hola,")
    return {
        "greeting": greeting,
        "presentation": "",
        "problem": "",
        "solution": "",
        "benefits": "",
        "cta": "",
        "_role": role,
        "_product_name": product_name,
        "_company": company,
        "_sender": sender,
        "_brand": brand,
        "_variant": variant,
        "_channel": ch,
        "_bank_body": body,
        "_bank_id": rendered.template_id,
    }


def assemble_first_touch_body_from_sections(sections: dict[str, Any]) -> str:
    from app.services.outbound_text_normalize import normalize_outbound_email_body

    bank_body = str(sections.get("_bank_body") or "").strip()
    if bank_body:
        return normalize_outbound_email_body(bank_body)

    opening: list[str] = []
    for key in ("greeting", "presentation"):
        val = str(sections.get(key) or "").strip()
        if val:
            opening.append(val)
    value_bits: list[str] = []
    for key in ("problem", "solution", "benefits"):
        val = str(sections.get(key) or "").strip()
        if not val:
            continue
        value_bits.append(val if val.endswith((".", "?", "!")) else f"{val}.")
    parts: list[str] = []
    if opening:
        parts.append("\n".join(opening))
    if value_bits:
        parts.append("\n\n".join(value_bits))
    cta = str(sections.get("cta") or "").strip()
    if cta:
        parts.append(cta)
    return normalize_outbound_email_body("\n\n".join(parts))


def build_follow_up_body(
    *,
    channel: str,
    variant: FollowUpVariant,
    prospect: dict[str, Any],
    campaign: dict[str, Any] | None = None,
    product: dict[str, Any] | None = None,
    step_day: int = 0,
) -> str:
    """Follow-up desde el banco determinístico."""
    del variant
    from app.services.cold_message_bank import render_cold_bank_touch

    campaign = campaign or {}
    rendered = render_cold_bank_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        prior_touches=[{"channel": channel, "body": "prior"}],
        first_touch=False,
        step_day=step_day,
    )
    return rendered.body

