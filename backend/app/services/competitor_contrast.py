"""
Objeción «ya usamos X» / «diferencia con X»: reconocer (si aplica), buscar contexto,
comparar lado a lado con el producto de la campaña.

Fail-open: si Brave/DDG/OpenAI fallan, cae a hechos curados + plantilla hablada.
No toca pipelines de envío mail / WhatsApp / LinkedIn.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL_SEC = 6 * 3600
_MAX_SNIPPETS = 3
_SEARCH_TIMEOUT_HINT = 2.5

_STOP = frozenset(
    {
        "que",
        "qué",
        "esto",
        "eso",
        "hoy",
        "ahora",
        "otra",
        "otro",
        "herramienta",
        "software",
        "plataforma",
        "sistema",
        "solucion",
        "solución",
        "algo",
        "nada",
        "nosotros",
        "ustedes",
        "ya",
        "para",
        "recursos",
        "humanos",
        "rrhh",
        "ventas",
        # Genéricos / frases de producto propio (no son competidores).
        "con",
        "de",
        "del",
        "los",
        "las",
        "una",
        "uno",
        "equipo",
        "equipos",
        "grupo",
        "grupos",
        "gente",
        "personas",
        "piloto",
        "outbound",
        "inbound",
        "sdr",
        "sdrs",
        "team",
        "teams",
        "uso",
        "usamos",
        "nexus",
        "nexus sales",
        # Verbos de agenda / charla (no son tools): «antes de agendar».
        "agendar",
        "agendo",
        "agenda",
        "hablar",
        "llamar",
        "reunir",
        "reunirnos",
        "charlar",
        "ver",
        "saber",
    }
)

# Nombre de tool: 1–4 tokens (Sales Navigator, People Data Labs, Apollo.io).
_TOOL_NAME_CAPTURE = (
    r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9+][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9+.\-]*"
    r"(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9+][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9+.\-]*){0,3})"
)

# Ojo: no usar bare «uso»/«tengo» — matchea «en uso con equipos…» (falso positivo).
_ALREADY_USE_RE = re.compile(
    r"\b(?:"
    r"ya\s+usamos|ya\s+tenemos|ya\s+uso|ya\s+tengo|"
    r"estamos\s+con|estamos\s+usando|trabajamos\s+con|"
    r"usamos|tenemos|"
    r"already\s+use|already\s+using|we\s+use|we\s+have"
    r")\s+(?:(?:un|una|el|la|our|a|an)\s+)?"
    + _TOOL_NAME_CAPTURE
    + r"(?:\s+(?:para|for)\s+[\w\s]{0,40})?",
    re.I,
)

# «hacen lo mismo que Lusha», «igual que Apollo», «parecido a HubSpot»
_SAME_AS_RE = re.compile(
    r"\b(?:"
    r"(?:hacen|hacemos|hace|haces)?\s*(?:lo\s+)?mismo\s+que|"
    r"igual\s+(?:que|a)|"
    r"parecido\s+a|"
    r"similar\s+a|"
    r"same\s+as|"
    r"just\s+like"
    r")\s+(?:(?:un|una|el|la|a|an)\s+)?"
    + _TOOL_NAME_CAPTURE,
    re.I,
)

# «diferencia con Sales Navigator», «differenzia co un apolo» (audio)
_DIFF_WITH_RE = re.compile(
    r"(?:difer+en[csz]i\w*|compar\w*|vs\.?|versus)\s+"
    r"(?:(?:hay|es|ser[ií]a)\s+)*"
    r"(?:de\s+|hay\s+)?"
    r"(?:un|una|el|la|su|tu)?\s*"
    r"(?:con|contra|entre|vs\.?|versus|co)\s+"
    r"(?:un|una|el|la)?\s*"
    + _TOOL_NAME_CAPTURE
    + r"(?:\s*[\?¿!.,]|\s*$)",
    re.I,
)

# Tools conocidos en outbound / CRM (por si DDG está caído o el nombre viene suelto).
_KNOWN_TOOL_RE = re.compile(
    r"\b("
    r"Sales\s+Navigator|Apollo(?:\.io)?|Apolo|Lusha|Lucha|ZoomInfo|Seamless\.?AI|"
    r"Hunter(?:\.io)?|Lemlist|Instantly(?:\.ai)?|Outreach|Salesloft|Clay|"
    r"PhantomBuster|Expandi|HubSpot|Salesforce|Pipedrive|Gong|Chorus|"
    r"Reply\.io|Woodpecker|Prospeo|Clearbit|People\s*Data\s*Labs|PDL|"
    r"BambooHR|Factorial|HiBob|Personio"
    r")\b",
    re.I,
)

# Typos / audio → nombre canónico
_TOOL_ALIASES: dict[str, str] = {
    "apolo": "Apollo",
    "apollo": "Apollo",
    "apollo.io": "Apollo.io",
    "lucha": "Lusha",
    "lusha": "Lusha",
    "salesnavigator": "Sales Navigator",
    "sales navigator": "Sales Navigator",
}

# Hechos públicos cortos (categoría de producto). No inventar precios ni features raros.
_KNOWN_TOOL_FACTS: dict[str, str] = {
    "apollo": (
        "Apollo.io — sales intelligence + engagement: base de contactos y "
        "secuencias de outreach (mail/llamadas), más orientada a datos y cadencias."
    ),
    "apollo.io": (
        "Apollo.io — sales intelligence + engagement: base de contactos y "
        "secuencias de outreach (mail/llamadas), más orientada a datos y cadencias."
    ),
    "lusha": (
        "Lusha — sales intelligence: datos de contacto y empresa para prospección; "
        "no es un orquestador multicanal de secuencia completa."
    ),
    "salesnavigator": (
        "Sales Navigator — búsqueda y filtros de LinkedIn para encontrar leads; "
        "no ejecuta secuencias de email + WhatsApp + LinkedIn en un solo flujo con IA."
    ),
    "sales navigator": (
        "Sales Navigator — búsqueda y filtros de LinkedIn para encontrar leads; "
        "no ejecuta secuencias de email + WhatsApp + LinkedIn en un solo flujo con IA."
    ),
    "zoominfo": (
        "ZoomInfo — base B2B de contactos/empresas y señales; fuerte en datos, "
        "no en unificar WhatsApp + LinkedIn + mail en un solo flujo SDR."
    ),
    "seamlessai": (
        "Seamless.AI — búsqueda de contactos y datos de ventas; foco en enrichment, "
        "no en ejecución multicanal con IA de punta a punta."
    ),
    "seamless.ai": (
        "Seamless.AI — búsqueda de contactos y datos de ventas; foco en enrichment."
    ),
    "hunter": (
        "Hunter.io — búsqueda y verificación de emails; no cubre LinkedIn + WhatsApp "
        "como secuencia unificada."
    ),
    "hunter.io": (
        "Hunter.io — búsqueda y verificación de emails; no cubre LinkedIn + WhatsApp "
        "como secuencia unificada."
    ),
    "lemlist": (
        "Lemlist — secuencias de email cold con personalización; canal principal mail, "
        "no el trío mail + LinkedIn + WhatsApp en un solo proceso."
    ),
    "instantly": (
        "Instantly — infraestructura y secuencias de cold email a escala."
    ),
    "instantly.ai": (
        "Instantly — infraestructura y secuencias de cold email a escala."
    ),
    "outreach": (
        "Outreach — sales engagement enterprise (cadencias mail/llamada); "
        "menos foco en WhatsApp + LinkedIn asistido en un solo flujo liviano."
    ),
    "salesloft": (
        "Salesloft — sales engagement (cadencias y coaching); fuerte en mail/llamada."
    ),
    "clay": (
        "Clay — enrichment y workflows de datos para prospección; no es la bandeja "
        "de ejecución diaria del SDR en WhatsApp/LinkedIn."
    ),
    "hubspot": (
        "HubSpot — CRM + marketing/sales hub amplio; la prospección outbound "
        "multicanal no es su núcleo como producto único de SDR."
    ),
    "salesforce": (
        "Salesforce — CRM enterprise; la capa de prospección outbound se arma aparte."
    ),
    "pipedrive": (
        "Pipedrive — CRM de pipeline; no reemplaza secuencias outbound multicanal."
    ),
    "crm": (
        "Un CRM guarda el pipeline y la relación; no ejecuta solo la prospección "
        "outbound en mail + LinkedIn + WhatsApp."
    ),
    "excel": (
        "Excel/planillas sirven para listas; no automatizan el follow-up multicanal."
    ),
    "planilla": (
        "Las planillas organizan datos; no corren la secuencia de toques sola."
    ),
}


def competitor_research_enabled() -> bool:
    v = (os.getenv("NEXUS_COMPETITOR_RESEARCH") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _norm_tool_key(name: str) -> str:
    return re.sub(r"[\s_]+", "", (name or "").strip().lower())


def _clean_competitor_token(word: str | None) -> str:
    token = (word or "").strip(" .,;:!?¿¡\"'()[]")
    token = re.sub(r"\s+", " ", token)
    # «BambooHR para el equipo» → BambooHR
    token = re.split(
        r"\s+(?:para|for|en|de|del|con|que|y)\b",
        token,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    if not token or len(token) < 2:
        return ""
    parts = token.split()
    first = parts[0].lower()
    if first in _STOP:
        return ""
    # Toda la frase es genérica («equipos de outbound», «grupos dedicados»).
    if all(p.lower().strip(".,;:?¿!") in _STOP for p in parts):
        return ""
    # "30 a 15min" / duraciones: no son tools.
    if first[:1].isdigit():
        return ""
    # Alias / canónico
    key = _norm_tool_key(token)
    if key in _TOOL_ALIASES:
        return _TOOL_ALIASES[key]
    low = token.lower()
    if low in _TOOL_ALIASES:
        return _TOOL_ALIASES[low]
    if low in _STOP or key in {_norm_tool_key(s) for s in _STOP}:
        return ""
    return token


def sanitize_competitor_hint(hint: str | None, *, campaign: Any | None = None) -> str:
    """Limpia hints del agente; vacío si es producto propio o genérico."""
    raw = (hint or "").strip()
    if not raw or is_own_product_name(raw, campaign):
        return ""
    # Hints basura del orquestador LLM («precio Nexus Sales», «antes de agendar»).
    low = raw.lower()
    if re.search(
        r"\b(?:precio|pricing|costo|n[uú]mero|tarifa|cobr\w*)\b",
        low,
        re.I,
    ):
        return ""
    if re.fullmatch(
        r"(?:antes\s+de\s+)?(?:agendar|agendo|agenda|hablar|llamar|reunir(?:nos)?|charlar)",
        low,
    ):
        return ""
    cleaned = _clean_competitor_token(raw)
    if not cleaned or is_own_product_name(cleaned, campaign):
        return ""
    return cleaned


def is_own_product_name(name: str | None, campaign: Any | None = None) -> bool:
    """True si el 'competidor' es en realidad el producto/marca propia (Nexus, etc.)."""
    raw = (name or "").strip().lower()
    if not raw:
        return False
    key = _norm_tool_key(raw)
    own = {"nexus", "nexussales", "costguard"}
    if key in own or key.startswith("nexus"):
        return True
    if campaign is not None:
        product = getattr(campaign, "product", None)
        pname = (getattr(product, "name", None) or "").strip().lower()
        if pname and (_norm_tool_key(pname) == key or key.startswith(_norm_tool_key(pname))):
            return True
        # Hints largos del agente: «Nexus Sales uso en equipos…»
        if pname and pname in raw:
            return True
    if "nexus" in raw:
        return True
    return False


def _strip_inbound_noise(raw: str) -> str:
    text = (raw or "").strip()
    # Prefijo de audio / notas de voz
    text = re.sub(r"^🎤\s*Audio:\s*", "", text, flags=re.I)
    text = re.sub(r"^\[Mandó una nota de voz\]\s*", "", text, flags=re.I)
    return text.strip()


def extract_competitor_name(inbound: str | None) -> str:
    """Nombre de la tool/competidor que mencionó el prospecto."""
    from app.services.linkedin_reply_compose import contrasted_tool, inbound_asks_integration

    raw = _strip_inbound_noise(inbound or "")
    if not raw:
        return ""
    # «¿Integran con HubSpot?» pide integración, no contraste vs HubSpot.
    if inbound_asks_integration(raw) and not re.search(
        r"\b(difer+en[csz]i\w*|vs\.?|versus|compar\w*|mejor\s+que|peor\s+que)\b",
        raw,
        re.I,
    ):
        return ""
    # Pregunta de adopción del propio producto («¿Nexus ya está en uso…?») ≠ competidor.
    if re.search(
        r"\b(?:nexus(?:\s+sales)?|ustedes|su\s+herramienta|su\s+producto)\b.{0,40}\b"
        r"(?:en\s+uso|piloto|en\s+producci[oó]n|clientes|equipos)\b",
        raw,
        re.I,
    ):
        return ""
    m = _ALREADY_USE_RE.search(raw)
    if m:
        word = _clean_competitor_token(m.group(1))
        if word and not is_own_product_name(word):
            return word
    m2 = _SAME_AS_RE.search(raw)
    if m2:
        word = _clean_competitor_token(m2.group(1))
        if word and not is_own_product_name(word):
            return word
    m3 = _DIFF_WITH_RE.search(raw)
    if m3:
        word = _clean_competitor_token(m3.group(1))
        if word and not is_own_product_name(word):
            return word
    known = _KNOWN_TOOL_RE.search(raw)
    if known:
        word = _clean_competitor_token(known.group(1))
        if word and not is_own_product_name(word):
            return word
    return _clean_competitor_token(contrasted_tool(raw)) or ""


def _cache_get(key: str) -> list[str] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, snippets = hit
    if time.monotonic() - ts > _CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return list(snippets)


def _cache_set(key: str, snippets: list[str]) -> None:
    _CACHE[key] = (time.monotonic(), list(snippets[:_MAX_SNIPPETS]))


def _curated_facts(tool_name: str) -> list[str]:
    key = _norm_tool_key(tool_name)
    if not key:
        return []
    if key in _KNOWN_TOOL_FACTS:
        return [_KNOWN_TOOL_FACTS[key]]
    # Apollo.io → apollo / apollo.io
    if key.startswith("apollo") or key == "apolo":
        return [_KNOWN_TOOL_FACTS["apollo"]]
    if key.startswith("salesnavigator") or key.startswith("salesnav"):
        return [_KNOWN_TOOL_FACTS["salesnavigator"]]
    if key.startswith("seamless"):
        return [_KNOWN_TOOL_FACTS["seamlessai"]]
    if key.startswith("hunter"):
        return [_KNOWN_TOOL_FACTS["hunter"]]
    if key.startswith("instantly"):
        return [_KNOWN_TOOL_FACTS["instantly"]]
    if key.startswith("lusha") or key == "lucha":
        return [_KNOWN_TOOL_FACTS["lusha"]]
    return []


def _wiki_facts(tool_name: str) -> list[str]:
    """Resumen Wikipedia si existe (User-Agent propio). Evita páginas ambiguas (Apollo espacial)."""
    name = (tool_name or "").strip()
    if not name or len(name) < 3:
        return []
    # Apollo sin .io / company → Wikipedia trae misiones espaciales; usar curado.
    if _norm_tool_key(name) in {"apollo"} or _norm_tool_key(name).startswith("apollo"):
        return []
    try:
        import httpx
    except Exception:
        return []
    headers = {
        "User-Agent": "NexusSalesBot/1.0 (competitor contrast; support@costguard.com.ar)",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=3.5, follow_redirects=True) as client:
            r = client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{name}",
                headers=headers,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            extract = (data.get("extract") or "").strip()
            title = (data.get("title") or name).strip()
            if not extract or len(extract) < 40:
                return []
            # Descartar desambiguaciones / no-software obvios
            low = extract.lower()
            if "may refer to" in low:
                return []
            if not any(
                k in low
                for k in (
                    "software",
                    "company",
                    "platform",
                    "crm",
                    "sales",
                    "marketing",
                    "data",
                    "saas",
                )
            ):
                return []
            line = f"{title} — {extract}"
            return [line[:320]]
    except Exception:
        logger.debug("competitor wiki failed tool=%r", name, exc_info=True)
        return []


def brief_competitor_facts(tool_name: str, *, country: str | None = None) -> list[str]:
    """
    Contexto público corto sobre la tool.
    Orden: cache → DDG → Wikipedia → hechos curados conocidos.
    """
    del country
    name = (tool_name or "").strip()
    if not name or not competitor_research_enabled():
        return []
    cache_key = name.lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    snippets: list[str] = []
    seen: set[str] = set()

    def _add(lines: list[str]) -> None:
        nonlocal snippets
        for line in lines:
            text = re.sub(r"\s+", " ", (line or "").strip())
            if not text:
                continue
            key = text[:160].lower()
            if key in seen:
                continue
            seen.add(key)
            snippets.append(text[:320])
            if len(snippets) >= _MAX_SNIPPETS:
                return

    try:
        from app.services.lead_sourcing.providers.web_search_backends import _search_ddg_html
    except Exception:
        _search_ddg_html = None  # type: ignore[assignment]

    if _search_ddg_html is not None:
        queries = [
            f"{name} software what it does",
            f"{name} herramienta para qué sirve",
        ]
        deadline = time.monotonic() + _SEARCH_TIMEOUT_HINT
        for q in queries:
            if time.monotonic() >= deadline or len(snippets) >= _MAX_SNIPPETS:
                break
            try:
                hits = _search_ddg_html(q, limit=2, provider="competitor_contrast")
            except Exception:
                logger.debug("competitor research ddg failed query=%r", q, exc_info=True)
                continue
            batch: list[str] = []
            for hit in hits or []:
                if isinstance(hit, (tuple, list)) and len(hit) >= 3:
                    title, snippet = str(hit[1] or ""), str(hit[2] or "")
                else:
                    title = str(getattr(hit, "title", "") or "")
                    snippet = str(getattr(hit, "snippet", "") or "")
                line = " — ".join(p for p in (title.strip(), snippet.strip()) if p)
                if line:
                    batch.append(line)
            _add(batch)

    # Hechos curados de tools conocidas: más estables que títulos SEO de DDG.
    curated = _curated_facts(name)
    if curated:
        # Preferir curado; DDG/wiki solo como extra si aportan algo distinto.
        snippets = []
        seen.clear()
        _add(curated)
    elif len(snippets) < 1:
        _add(_wiki_facts(name))

    _cache_set(cache_key, snippets)
    return snippets


def _product_brief(campaign: Any) -> dict[str, str]:
    from app.services.linkedin_reply_compose import (
        _notes_section,
        _product_payload,
        product_value_pitch,
    )

    product = _product_payload(campaign)
    prod_obj = getattr(campaign, "product", None)
    notes = str(getattr(prod_obj, "target_notes", None) or "") if prod_obj is not None else ""
    return {
        "name": (product.get("name") or "").strip() or "Lo nuestro",
        "value": product_value_pitch(campaign, max_chars=220),
        "description": (product.get("description") or "").strip()[:400],
        "objections": _notes_section(notes, "objeciones comunes", "objeciones")[:400],
        "benefits": _notes_section(notes, "beneficios principales", "beneficios")[:400],
    }


def inbound_claims_using_competitor(inbound: str | None) -> bool:
    """True solo si dijo que ya usa/tiene esa tool (no si solo pregunta la diferencia)."""
    raw = (inbound or "").strip()
    if not raw:
        return False
    # «¿Nexus ya está en uso…?» no es «ya usamos un competidor».
    if not extract_competitor_name(raw):
        return False
    return bool(_ALREADY_USE_RE.search(raw))


def _clip_fact(line: str, max_chars: int = 140) -> str:
    text = re.sub(r"\s+", " ", (line or "").strip())
    # Si viene "Nombre — resto", preferir el resto para no repetir el nombre.
    if " — " in text:
        text = text.split(" — ", 1)[1].strip()
    if len(text) <= max_chars:
        return text.rstrip(" .,;:—-")
    cut = text[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" .,;:—-") + "…"


# Beneficio de notas pegado al VP sin punto: «…interés real Reducción del…»
_JAMMED_BENEFIT_RE = re.compile(
    r"\b(real|manuales?|tiempo|horas|equipo|sdr|ventas|pipeline)\s+"
    r"(Reducci[oó]n|Ahorro|Mejora|Incremento|Mayor|Menos)\b",
    re.I,
)


def _spoken_our_edge(product: dict[str, str]) -> str:
    """
    Una oración limpia y hablada a partir del VP/beneficios.
    No brochure pegado ni pegotes tipo «…interés real Reducción del 60%…».
    """
    raw = (product.get("value") or product.get("description") or "").strip()
    raw = re.sub(r"\s+", " ", raw)
    raw = _JAMMED_BENEFIT_RE.sub(r"\1. \2", raw)
    # Una oración limpia del VP del producto de la campaña (cualquier vertical).
    # No inventar pitch de «prospección outbound» ni de Nexus.
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", raw) if p.strip()]
    if not parts and product.get("benefits"):
        first_ben = re.split(r"[.;]", product["benefits"], maxsplit=1)[0].strip()
        if first_ben:
            parts = [first_ben]
    if not parts:
        return ""
    s = parts[0].rstrip(" .,;")
    if s[0].islower():
        s = s[0].upper() + s[1:]
    text = s + "."
    if len(text) > 180:
        # Preferir corte en coma/clause antes que brochure entero.
        cut = text[:180]
        for sep in (". ", "; ", ", "):
            i = cut.rfind(sep)
            if i >= 80:
                return cut[: i + (1 if sep.startswith(".") else 0)].rstrip(" .,;") + "."
        text = cut.rsplit(" ", 1)[0].rstrip(".,;:") + "."
    return text


def _polish_contrast_text(text: str) -> str:
    """
    Evita el arranque mal puntuado:
    «Mirá la diferencia con Kaspr, Nexus Sales…» → «Mirá la diferencia: Nexus Sales…»
    Preserva saltos de burbuja (\n\n).
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    beats = [re.sub(r"[ \t]+", " ", b).strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    polished: list[str] = []
    for t in beats or [re.sub(r"\s+", " ", raw)]:
        m = re.match(
            r"^(Mirá|Mira)\s+la\s+diferencia\s+con\s+[^,.:;]+\s*[,:]\s*(.+)$",
            t,
            re.I,
        )
        if m:
            rest = (m.group(2) or "").strip()
            if rest:
                if rest[0].islower():
                    rest = rest[0].upper() + rest[1:]
                t = f"Mirá la diferencia: {rest}"
        m2 = re.match(
            r"^(Mirá|Mira)\s+la\s+diferencia\s+con\s+[^.]+\.\s*(.+)$",
            t,
            re.I,
        )
        if m2:
            rest = (m2.group(2) or "").strip()
            if rest:
                if rest[0].islower():
                    rest = rest[0].upper() + rest[1:]
                t = f"Mirá la diferencia: {rest}"
        polished.append(t)
    return "\n\n".join(polished)


def _template_contrast(
    *,
    product_name: str,
    other: str,
    product: dict[str, str],
    objections: str,
    facts: list[str],
    they_use: bool,
    close: str,
) -> str:
    spoken = _spoken_our_edge(product)
    if objections and other.lower() in objections.lower():
        body = re.split(r"[.;]", objections, maxsplit=1)[0].strip().rstrip(".") + "."
    else:
        other_bit = _clip_fact(facts[0]) if facts else ""
        our = spoken or f"{product_name} aporta otra capa en el día a día."
        # Oración propia que empieza por el producto de la campaña (no un pitch fijo).
        if product_name.lower() not in our.lower():
            our_line = f"{product_name} {our[0].lower() + our[1:] if our else our}"
        else:
            our_line = our
        our_line = our_line.rstrip(" .,;") + "."

        # Corto y punzante: 1–2 beats, no choclo.
        our_short = _clip_fact(our_line, max_chars=120)
        if they_use:
            body = (
                f"Si ya están con {other}, tiene sentido.\n\n"
                f"Mirá la diferencia: {our_short}"
            )
        elif other_bit:
            other_short = _clip_fact(other_bit, max_chars=90)
            body = (
                f"Mirá la diferencia: {our_short}\n\n"
                f"{other} va más a {other_short[0].lower() + other_short[1:] if other_short else other_short}."
            )
        else:
            body = (
                f"Mirá la diferencia: {our_short}\n\n"
                f"Con {other} lo contrastamos punto a punto en una call corta."
            )
    # Colapsar espacios dentro de línea; preservar saltos de burbuja.
    body = "\n\n".join(
        re.sub(r"[ \t]+", " ", ln).strip()
        for ln in re.split(r"\n\s*\n", body)
        if ln.strip()
    )
    body = re.sub(r"\.\s*\.", ". ", body)
    body = _polish_contrast_text(body)
    close = (close or "").strip()
    if close:
        if close.startswith("¿"):
            return f"{body.rstrip()}\n\n{close}"
        return f"{body.rstrip('. ')}\n\n{close}"
    return body


def _llm_contrast(
    *,
    product: dict[str, str],
    other: str,
    facts: list[str],
    they_use: bool,
    close: str,
) -> str | None:
    try:
        from app.services.openai_service import _chat, openai_configured
    except Exception:
        return None
    if not openai_configured():
        return None

    spoken = _spoken_our_edge(product)
    if facts:
        facts_block = "\n".join(f"- {s}" for s in facts[:_MAX_SNIPPETS])
        facts_section = (
            f"Hechos públicos sobre {other} (pueden estar incompletos):\n{facts_block}\n"
        )
    else:
        facts_section = (
            f"No hay hechos públicos frescos de {other}. "
            "NO inventes qué hace esa tool; hablá de la nuestra y proponé contrastar en call.\n"
        )

    if they_use:
        stance = (
            f"El prospecto indicó que ya usa o tiene {other}. "
            "Podés reconocerlo con suavidad (sin «sé que ya usan» dogmático)."
        )
        step1 = f"1) Reconocé su mención de {other} sin despreciarlo.\n"
    else:
        stance = (
            f"El prospecto preguntó la diferencia / comparación con {other}. "
            "NO digas que ya la usan, ni «sé que están familiarizados», ni asumas que es su stack."
        )
        step1 = (
            f"1) Arrancá con «Mirá la diferencia:» (DOS PUNTOS) y acto seguido "
            f"una oración completa que empiece con {product['name']}.\n"
            f"PROHIBIDO el arranque «Mirá la diferencia con {other}, {product['name']}…» "
            "(coma mal pegada / oración rota).\n"
        )

    prompt = (
        f"{stance} Respondé en español rioplatense, MÁXIMO 2 oraciones cortas + CTA "
        "(~35 palabras). Separá beats con una línea en blanco.\n"
        "Estructura (comparación real, corta, genera interés):\n"
        f"{step1}"
        f"2) Una sola idea de qué hace {product['name']} "
        f"(reformulá; PROHIBIDO pegar VP literal o % tal cual).\n"
        f"3) Una frase corta de {other} (si no hay hechos: 'lo vemos en call'; no inventes).\n"
        "4) CTA call en beat aparte.\n"
        "PROHIBIDO párrafos largos / choclo de 3+ oraciones densas.\n"
        "Calidad: cada idea termina en punto; sin markdown, sin emojis, sin listas.\n"
        f"PROHIBIDO: inventar integraciones/precios; decir que {other} es malo; "
        f"afirmar que ya usan {other} si no lo dijeron; copiar value_proposition verbatim; "
        f"escribir «Mirá la diferencia con {other}, …».\n\n"
        f"Nuestro producto: {product['name']}\n"
        f"Idea de valor (REFORMULÁ, no copies): {spoken or product['value'] or product['description'] or '—'}\n"
        f"Beneficios (pista, no copies): {product['benefits'] or '—'}\n"
        f"Notas de objeción (si hay): {product['objections'] or '—'}\n"
        f"{facts_section}"
    )
    if close:
        prompt += f"\nCierre sugerido (podés adaptar): {close}\n"
    try:
        out = _chat(
            "Sos un SDR consultivo. Mensajes cortos, bien escritos, sin markdown, sin emojis.",
            prompt,
            temperature=0.45,
            max_output_tokens=220,
        )
    except Exception:
        logger.debug("competitor contrast llm failed", exc_info=True)
        return None
    text = (out or "").strip()
    if not text or len(text) < 20:
        return None
    # Rechazar si el modelo pegó el VP crudo casi entero (>80 chars en común).
    vp = (product.get("value") or "").strip()
    if vp and len(vp) > 60:
        chunk = vp[:80].lower()
        if chunk in text.lower():
            logger.info("competitor contrast llm rejected: pasted value prop")
            return None
    return _polish_contrast_text(text)


def extract_competitor_from_texts(*texts: str | None) -> str:
    """Primer nombre de tool encontrado en una lista de mensajes (actual + historial)."""
    for raw in texts:
        if not raw:
            continue
        name = extract_competitor_name(raw)
        if name:
            return name
    return ""


def build_competitor_contrast_reply(
    campaign: Any,
    inbound: str,
    *,
    close: str = "",
    country: str | None = None,
    prior_inbounds: list[str] | None = None,
) -> str:
    """
    Respuesta educada a «ya usamos X» / «diferencia con X».
    Research opcional + LLM corto; si falla, plantilla con comparación hablada.
    """
    product = _product_brief(campaign)
    other = extract_competitor_from_texts(inbound, *(prior_inbounds or []))
    if not other:
        spoken = _spoken_our_edge(product)
        if spoken:
            body = (
                f"Decime contra qué herramienta lo querés comparar. "
                f"En corto: {product['name']} — {spoken[0].lower() + spoken[1:] if spoken else spoken}"
            )
        else:
            body = (
                f"Decime contra qué herramienta lo querés comparar y te marco "
                f"la diferencia de {product['name']} punto a punto."
            )
        close_s = (close or "").strip()
        if close_s:
            return (
                f"{body.rstrip()} {close_s}"
                if close_s.startswith("¿")
                else f"{body.rstrip('. ')}. {close_s}"
            )
        return body

    they_use = inbound_claims_using_competitor(inbound)
    facts: list[str] = []
    try:
        facts = brief_competitor_facts(other, country=country)
    except Exception:
        logger.debug("competitor facts failed tool=%s", other, exc_info=True)
        facts = []
    if not facts:
        facts = _curated_facts(other)

    reasoned = None
    try:
        reasoned = _llm_contrast(
            product=product,
            other=other,
            facts=facts,
            they_use=they_use,
            close=close,
        )
    except Exception:
        reasoned = None
    if reasoned:
        return reasoned

    return _template_contrast(
        product_name=product["name"],
        other=other,
        product=product,
        objections=product["objections"],
        facts=facts,
        they_use=they_use,
        close=close,
    )


def competitor_context_for_education(inbound: str, campaign: Any) -> str:
    """Bloque corto para inyectar en education del LLM de canal (mail/WA/LI)."""
    from app.services.linkedin_reply_compose import inbound_asks_integration

    if inbound_asks_integration(inbound) and not re.search(
        r"\b(difer+en[csz]i\w*|vs\.?|versus|compar\w*)\b", inbound or "", re.I
    ):
        product = _product_brief(campaign)
        tool_hint = ""
        known = _KNOWN_TOOL_RE.search(inbound or "")
        if known:
            tool_hint = _clean_competitor_token(known.group(1)) or ""
        label = tool_hint or "esa herramienta"
        return "\n".join(
            [
                f"PREGUNTA DE INTEGRACIÓN (no comparación): el prospecto pregunta si "
                f"{product['name']} se integra / conecta con «{label}».",
                "Si la ficha NO documenta la integración con certeza: NO inventes sí ni no. "
                "Decí que lo ven / lo resuelven en una call corta con su setup. "
                "PROHIBIDO armar un 'vs' / 'Mirá la diferencia' / contraste de productos.",
                f"Nuestro producto: {product['name']}.",
            ]
        )

    other = extract_competitor_name(inbound)
    if not other:
        return ""
    product = _product_brief(campaign)
    facts = brief_competitor_facts(other) or _curated_facts(other)
    they_use = inbound_claims_using_competitor(inbound)
    spoken = _spoken_our_edge(product)
    if they_use:
        lines = [
            f"OBJECIÓN COMPETIDOR: el prospecto indicó que usa «{other}».",
            "Reconocé sin dogmas («sé que ya usan…»); compará lado a lado; CTA call.",
        ]
    else:
        lines = [
            f"COMPARACIÓN: el prospecto preguntó por «{other}» (no asumió que ya la usa).",
            "Compará de verdad: qué hace lo nuestro vs qué hace esa tool (solo con hechos); CTA call.",
            "PROHIBIDO afirmar que ya usan esa tool; PROHIBIDO pegar el VP literal.",
        ]
    lines.extend(
        [
            f"Nuestro producto: {product['name']}.",
            f"Idea nuestra (reformular): {spoken or product['value'] or product['description'] or '—'}.",
        ]
    )
    if facts:
        lines.append("Contexto público de esa tool:")
        lines.extend(f"- {s}" for s in facts[:_MAX_SNIPPETS])
    return "\n".join(lines)
