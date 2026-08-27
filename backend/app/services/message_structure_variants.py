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
FirstTouchVariant = Literal["problem_offer", "context_value", "direct_short"]
FollowUpVariant = Literal["soft_summary", "soft_invite"]

FIRST_TOUCH_VARIANTS: tuple[FirstTouchVariant, ...] = (
    "problem_offer",
    "context_value",
    "direct_short",
)
FOLLOW_UP_VARIANTS: tuple[FollowUpVariant, ...] = (
    "soft_summary",
    "soft_invite",
)

_FIRST_LABELS = {
    "problem_offer": "E1/L1/W1 — Problema → oferta → resultado → CTA",
    "context_value": "E2/L2/W2 — Contexto personalizado → valor → CTA",
    "direct_short": "E3/L3/W3 — Directo / corto → valor en una idea → CTA",
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
  PROHIBIDO conjugación rota («Con {producto} incrementa/automatiza…» → usá «incrementamos/automatizamos»).
  PROHIBIDO buzzwords vacíos («menos fricción operativa», «mejorar la eficiencia», «optimizar procesos»).
  PROHIBIDO que todos los mensajes repitan la misma explicación pegada de la ficha.
- Objetivo: agendar reunión breve. NUNCA cerrar la venta.
- sections.greeting: "Hola {first}," (o equivalente natural del canal).
- Firma email (si aplica): Saludos, {sender}
"""

    if variant == "problem_offer":
        shape = """
FORMA (Problema → oferta → resultado → CTA):
sections.presentation: "Soy {sender} de {brand}." (+ gancho SOLO con evidencia del brief; si no hay, sin inventar).
sections.problem: BLOQUE amplio — por qué escribís ahora (ángulo de rol/empresa/sector + necesidad). 1–2 oraciones. Todo libre.
sections.solution: BLOQUE amplio — qué hace la marca/producto y cómo, en lenguaje de ESA empresa. 1–2 oraciones.
sections.benefits: BLOQUE amplio — qué cambia para alguien como el prospecto. Concreto. 1 oración.
sections.cta: pregunta para coordinar reunión breve (?).
body = greeting + presentation + problem + solution + benefits + cta.
""".replace("{sender}", sender).replace("{brand}", brand)
    elif variant == "context_value":
        shape = f"""
FORMA (Contexto personalizado → valor → CTA):
sections.presentation: puede ser "Soy {sender} de {brand}." O vacío si el contexto ya presenta (email/LinkedIn).
sections.problem: BLOQUE amplio de contexto anclado a rol/empresa (dato real del brief; si no hay research: ancla CRM suave al cargo/compañía). NO inventar.
sections.solution: BLOQUE amplio de valor (2–3 oraciones email; 2–3 LinkedIn; 1 WhatsApp) — qué ofrecen y para qué situación encaja. Todo libre.
sections.benefits: vacío o una línea extra solo si aporta.
sections.cta: proponer charla corta / coordinar 15 min (?).
body = greeting + (presentation si hay) + problem + solution (+ benefits) + cta.
"""
    else:
        shape = f"""
FORMA (Directo / corto):
sections.presentation: "Soy {sender} de {brand}." o "Soy {sender} ({brand})." según canal.
sections.problem: vacío preferible.
sections.solution: BLOQUE ÚNICO amplio (mezcla por qué + qué + para qué). Email 3–5 oraciones; LinkedIn 2–3; WhatsApp 1 oración corta. Todo libre.
sections.benefits: vacío.
sections.cta: UNA pregunta clara para agendar (?).
body = greeting + presentation + solution + cta (máxima densidad, sin relleno).
"""

    if ch == "email":
        length = (
            "Email: ideal 70-110 palabras (55-120 ok). SUBJECT breve, curiosidad, "
            "personalizado a la empresa del prospecto — sin spam.\n"
        )
    elif ch == "linkedin":
        length = (
            "LinkedIn: corto, pero MÁS desarrollado que WhatsApp. "
            "Ideal ~280-480 caracteres (máx ~550). Sin subject.\n"
            "OBLIGATORIO párrafos cortos separados por línea en blanco "
            "(saludo+presentación · valor · CTA). PROHIBIDO un solo muro de texto.\n"
            "PROHIBIDO pegar o parafrasear casi literal la ficha Producto/servicio "
            "(value_proposition + description enteras). "
            "Reescribí valor en 2–3 oraciones conversacionales. "
            "Si la ficha empieza con «Automatiza…», NO lo copies: "
            "decí p.ej. «Con {producto} automatizamos…» en una frase corta.\n"
        )
    else:
        length = (
            "WhatsApp: MÁS CORTO que LinkedIn. Informal, chill, rioplatense. "
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
) -> str:
    """Follow-up outbound (sin respuesta): modo despedida suave + «quedo atento»."""
    ch = _norm_channel(channel)
    label = _FOLLOW_LABELS[variant]
    day = f"Día {step_day}" if step_day else "seguimiento"

    base = f"""VARIANTE AUTOMÁTICA DE FOLLOW-UP ({day}): {label}
MODO DESPEDIDA SUAVE (sin respuesta previa):
- NO re-pitchear el producto completo del Día 1.
- NO culpar («¿revisaste mi mensaje?», «sin respuesta»).
- Cierre respetuoso: dejar la puerta abierta y terminar con «Quedo atento» (o «Quedo atento a lo que te quede mejor»).
- Objetivo sigue siendo agendar si hay interés; sin presión agresiva.
"""

    if ch == "email" and variant == "soft_summary":
        return (
            base
            + """
EMAIL · EF1 — Resumen corto + puerta abierta:
1–2 oraciones que retoman el hilo (ángulo de valor breve, editable; sin plantilla fija).
Luego: si le parece útil, coordinar reunión breve.
Cierre: Quedo atento.
50-90 palabras. Preferí mismo hilo (Re:).
"""
        )
    if ch == "email":
        return (
            base
            + """
EMAIL · EF2 — Pregunta suave + disponibilidad:
1 oración de contexto («te había escrito por…» — libre, sin audiencia fija).
Pregunta: ¿tiene sentido una llamada corta en los próximos días?
Cierre: Quedo atento a lo que te quede mejor.
50-90 palabras. Preferí mismo hilo (Re:).
"""
        )
    if ch == "linkedin" and variant == "soft_summary":
        return (
            base
            + """
LINKEDIN · LF1 — Cierre en una idea:
1–2 oraciones en 2 párrafos cortos: recordatorio breve + valor en una línea.
Si encaja, charla corta.
Cierre: Quedo atento.
180-400 caracteres. Más espacio que WhatsApp; sin muro de texto.
"""
        )
    if ch == "linkedin":
        return (
            base
            + """
LINKEDIN · LF2 — Invitación sin presión (NO binaria agresiva):
1 oración anclada al rol/empresa o al hilo (párrafo 1).
Invitar: cuando le venga bien, 10–15 min (párrafo 2).
Cierre: Quedo atento.
180-380 caracteres.
"""
        )
    if ch == "whatsapp" and variant == "soft_summary":
        return (
            base
            + """
WHATSAPP · WF1 — Último toque corto y chill:
«te escribo por última vez sobre esto» (o equivalente natural).
1 idea de valor/contexto editable.
Si sirve, agendar unos minutos.
Cierre: Quedo atento.
2 micro-párrafos (~15-35 palabras). MÁS corto que LinkedIn.
"""
        )
    return (
        base
        + """
WHATSAPP · WF2 — Directo, liviano y chill:
Pregunta corta: ¿te calza una charla corta esta semana?
Opcional: media oración de contexto solo si aporta.
Cierre: Quedo atento.
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
    t = re.sub(r"\s+", " ", (text or "").strip()).rstrip(" .,;")
    if len(t) <= max_chars:
        return t
    cut = t[: max_chars + 1]
    for sep in (". ", "; ", ", "):
        idx = cut.rfind(sep)
        if idx >= max(40, max_chars // 3):
            return cut[:idx].rstrip(" .,;")
    sp = cut.rfind(" ")
    if sp >= 40:
        return cut[:sp].rstrip(" .,;")
    return t[:max_chars].rstrip(" .,;")


def _strip_leading_product_name(text: str, product_name: str) -> str:
    t = (text or "").strip()
    name = (product_name or "").strip()
    if name and t.lower().startswith(name.lower()):
        t = t[len(name) :].lstrip(" ,.-:")
    return t


# Frases de ficha que no deben llegar al mensaje (buzzwords / marketing interno).
_GENERIC_FLUFF_RE = re.compile(
    r"\b(?:"
    r"reduc(?:iendo|e|ir)?\s+la\s+fricci[oó]n\s+operativa|"
    r"menos\s+fricci[oó]n\s+operativa|"
    r"mejorar(?:\s+la)?\s+eficiencia|"
    r"optimizar\s+(?:los\s+)?procesos|"
    r"mensajes\s+personalizados\s+generados\s+por\s+IA|"
    r"IA\s+entrenada\s+en\s+el\s+producto\s+del\s+cliente|"
    r"automatizaci[oó]n\s+asistida|"
    r"control\s+humano|"
    r"combin(?:a|ando)\s+IA|"
    r"plataforma\s+multicanal\s+de\s+outreach\s+comercial"
    r")\b[,.]?",
    re.I,
)

_THIRD_PERSON_START = re.compile(
    r"^(automatiza|orquesta|centraliza|integra|consolida|permite|reduce|"
    r"incrementa|aumenta|acelera|simplifica|facilita|genera|conecta|"
    r"organiza|gestiona|ofrece|ayuda|mejora|optimiza|elimina|ahorra|"
    r"multiplica|potencia|impulsa)\b",
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
}


def _scrub_marketing_fluff(text: str) -> str:
    t = _GENERIC_FLUFF_RE.sub(" ", text or "")
    t = re.sub(r"\s*,\s*,+", ", ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",\s*\.", ".", t)
    return t.strip(" ,.;")


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
) -> str:
    """
    Reescribe el valor del producto en voz de SDR (nosotros…), sin copiar la ficha.
    Evita conjugaciones rotas («Con X incrementa…») y buzzwords vacíos.
    """
    ch = _norm_channel(channel)
    max_chars = 200 if ch == "email" else (170 if ch == "linkedin" else 110)
    raw_vp = re.sub(r"\s+", " ", ((product or {}).get("value_proposition") or "").strip()).rstrip(".")
    raw_vp = _strip_leading_product_name(raw_vp, product_name)
    cleaned = _scrub_marketing_fluff(raw_vp)

    hooks = _concrete_hooks(cleaned) or _concrete_hooks(raw_vp)
    pct = next((h for h in hooks if "%" in h), "")
    channels = next(
        (h for h in hooks if "linkedin" in h.lower() or "whatsapp" in h.lower()),
        "",
    )

    # Variantes cortas ancladas a hechos concretos (no a la prosa de la ficha).
    variants: list[str] = []
    if pct and channels:
        variants.append(
            f"Con {product_name} automatizamos entre {pct} del trabajo manual de "
            f"prospección y contacto por {channels}"
        )
        variants.append(
            f"Con {product_name} bajamos el trabajo manual de outbound "
            f"(cerca de {pct}) unificando {channels}"
        )
    elif pct:
        variants.append(
            f"Con {product_name} automatizamos alrededor de {pct} de las tareas "
            "manuales de prospección outbound"
        )
    elif channels:
        variants.append(
            f"Con {product_name} centralizamos la prospección y el contacto por {channels} "
            "para agendar más reuniones"
        )
        variants.append(
            f"Con {product_name} orquestamos el outreach por {channels} "
            "sin perseguir cada lead a mano"
        )

    if re.search(r"\breuni", cleaned, re.I) or re.search(r"\breuni", raw_vp, re.I):
        variants.append(
            f"Con {product_name} ayudamos a conseguir más reuniones comerciales "
            "con menos seguimiento manual"
        )

    # Si quedó texto usable tras scrub: conjugar a «nosotros» (nunca pegar 3ª persona cruda).
    if cleaned and len(cleaned) >= 18:
        m = _THIRD_PERSON_START.match(cleaned)
        if m:
            verb = _to_nosotros_verb(m.group(1))
            rest = cleaned[m.end() :].strip(" ,")
            if rest:
                variants.append(f"Con {product_name} {verb} {rest}")
        elif re.match(
            r"^(ayudamos|automatizamos|centralizamos|orquestamos|incrementamos|"
            r"ofrecemos|aportamos)\b",
            cleaned,
            re.I,
        ):
            rest = cleaned[0].lower() + cleaned[1:] if cleaned else cleaned
            variants.append(f"Con {product_name} {rest}")
        else:
            # Ficha descriptiva (ej. producto físico): no inventar pitch SaaS.
            low_c = cleaned[0].lower() + cleaned[1:] if cleaned else cleaned
            variants.append(f"Con {product_name} ofrecemos {low_c}")
            variants.append(f"{product_name}: {cleaned[0].upper() + cleaned[1:]}")

    if not variants:
        variants.append(
            f"Con {product_name} aportamos valor concreto según lo que necesitás"
        )

    idx = 0
    if seed and len(variants) > 1:
        digest = 0
        for ch_i in seed.encode("utf-8"):
            digest = (digest * 31 + ch_i) & 0xFFFFFFFF
        idx = digest % len(variants)
    blurb = variants[idx]
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
    return _clip_sentence(blurb, max_chars=max_chars)


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
) -> str:
    """
    Valor corto para CRM/fallback: reescribe la ficha, no la pega.
    LinkedIn/WhatsApp quedan más cortos que email.
    """
    return _rewrite_product_value(
        product,
        product_name=product_name,
        channel=channel,
        seed=seed,
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

