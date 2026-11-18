"""Normalización de texto plano outbound (email / mensajes)."""

from __future__ import annotations

import re
from typing import Any, Literal, Sequence

_GREETING_RE = re.compile(
    r"^(hola|buen[oa]s(?:\s+d[ií]as|\s+tardes|\s+noches)?|hey|hi|hello)\b",
    re.IGNORECASE,
)

# Solo saludo real («Hola Test,» / «Buenos días,»). NO «Buena pregunta».
_OPENING_GREETING_PREFIX = re.compile(
    r"^(?:hola|buen[oa]s(?:\s+d[ií]as|\s+tardes|\s+noches)?|hey|hi|hello)"
    r"(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][\wÁÉÍÓÚÜÑáéíóúüñ'’-]{0,40})?"
    r"\s*,\s*",
    re.IGNORECASE,
)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÜÑ¿¡0-9])")

# Cierre / cambio de tema (CTA suave). Ahí sí va línea en blanco.
_TOPIC_CTA_START_RE = re.compile(
    r"^(?:"
    r"si\s+quer[eé]s|si\s+quieres|si\s+te\s+parece|si\s+te\s+sirve|si\s+te\s+viene|"
    r"cuando\s+quieras|decime\s+si|avisame\s+si|avisame|"
    r"puedo\s+contarte|puedo\s+mostrarte|puedo\s+explicarte|"
    r"¿(?:te\s+parece|quer[eé]s|agendamos|coordinamos|te\s+queda)|"
    r"quedamos|coordinamos|agendamos"
    r")\b",
    re.IGNORECASE,
)


def _peel_opening_greeting(text: str) -> tuple[str, str]:
    """Separa 'Hola X,' del cuerpo aunque venga todo en una línea."""
    raw = (text or "").strip()
    if not raw:
        return "", ""
    # Primera línea corta tipo saludo.
    first, _, rest = raw.partition("\n")
    first = first.strip()
    if (
        _GREETING_RE.match(first)
        and len(first) <= 48
        and (first.endswith(",") or not rest.strip())
    ):
        body = rest.strip()
        if not body and "," in first:
            head, _, after = first.partition(",")
            if after.strip() and len(after.strip()) > 24:
                return f"{head.strip()},", after.strip()
        return first if first.endswith(",") else f"{first},", body
    # «Hola Test, Nexus Sales ya…» pegado en un solo renglón largo.
    m = _OPENING_GREETING_PREFIX.match(raw)
    if m and len(raw) > m.end() + 24:
        greet = raw[: m.end()].strip().rstrip(",").strip()
        return f"{greet},", raw[m.end() :].strip()
    return "", raw


_MEETING_CTA_SENT_RE = re.compile(
    r"(?i)\b(?:llamada|reuni[oó]n|charla|videollamada|call|meet|coordin\w*|agend\w*|"
    r"lo\s+vemos|hablamos|minutos?|martes|mi[eé]rcoles|jueves|viernes|lunes|"
    r"a\s+las\s+\d{1,2})"
)


def _split_closing_cta(body: str) -> tuple[str, str]:
    """Separa el cierre CTA del bloque de respuesta. Un solo CTA (el mejor)."""
    raw = (body or "").strip()
    if not raw:
        return "", ""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(raw) if s.strip()]
    if len(sentences) < 2:
        return raw, ""
    # Recolectar CTAs finales; si hay varios, quedarse con UNO (prioriza horarios).
    cta_idxs: list[int] = []
    for i in range(len(sentences) - 1, 0, -1):
        if _TOPIC_CTA_START_RE.match(sentences[i]) and _MEETING_CTA_SENT_RE.search(sentences[i]):
            cta_idxs.append(i)
        elif cta_idxs:
            break
        else:
            break
    if not cta_idxs:
        return raw, ""
    cta_idxs.sort()
    preferred = cta_idxs[-1]
    for i in cta_idxs:
        if re.search(r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|\d{1,2}:\d{2})\b", sentences[i], re.I):
            preferred = i
            break
    drop = set(cta_idxs)
    main_parts = [sentences[i] for i in range(len(sentences)) if i not in drop]
    main = " ".join(main_parts).strip()
    cta = sentences[preferred].strip()
    return main or raw, cta


_SLOT_BLOCK_RE = re.compile(
    r"(?:estos\s+horarios|horarios?\s+(?:me\s+)?(?:quedan\s+)?libres|"
    r"tengo\s+(?:estos\s+)?horarios|•\s*\w+\s+\d{1,2}:\d{2})",
    re.IGNORECASE,
)


def _flatten_soft_wraps(text: str) -> str:
    t = re.sub(r"\n+(?=[a-záéíóúüñ])", " ", text or "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    return t.strip()


def _paragraph_units(block: str) -> list[str]:
    """Parte un bloque: respuesta | CTA | (deja slots como tema aparte)."""
    raw = _flatten_soft_wraps(block)
    if not raw:
        return []
    # Bloque de agenda / bullets: no meter mano, es otro tema.
    if _SLOT_BLOCK_RE.search(raw):
        return [re.sub(r"\s*\n\s*", "\n", raw).strip()]
    joined = re.sub(r"\s*\n\s*", " ", raw).strip()
    main, cta = _split_closing_cta(joined)
    out: list[str] = []
    if main:
        out.append(main)
    if cta:
        out.append(cta)
    return out or [joined]


def ensure_email_topic_paragraphs(text: str | None, *, max_paras: int = 6) -> str:
    """
    Mail: espacio en blanco al cambiar de tema (no 1 oración = 1 párrafo).
    Típico: saludo · presentación/valor · UN CTA · firma.
    Nunca fusionar dos CTAs en un solo párrafo.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    greeting, body = _peel_opening_greeting(raw)
    if not body:
        return greeting or raw

    body = _flatten_soft_wraps(body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    # Aunque ya haya \\n\\n (p.ej. respuesta + horarios), partir CTA dentro de cada bloque.
    chunks = re.split(r"\n\s*\n+", body)
    paras: list[str] = []
    if greeting:
        paras.append(greeting)
    for chunk in chunks:
        paras.extend(_paragraph_units(chunk))
    def _is_cta_para(p: str) -> bool:
        return bool(_TOPIC_CTA_START_RE.match(p.strip()) and _MEETING_CTA_SENT_RE.search(p))

    def _pick_one_cta(cta_paras: list[str]) -> str:
        if not cta_paras:
            return ""
        chosen = cta_paras[-1]
        for p in cta_paras:
            if re.search(
                r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|\d{1,2}:\d{2})\b",
                p,
                re.I,
            ):
                return p
        return chosen

    # Dedup vacíos / colapsar de más — sin aplastar CTAs juntos.
    paras = [p.strip() for p in paras if p and p.strip()]
    if len(paras) > max_paras:
        head = paras[0]
        # Firma corta al final (Saludos / nombre).
        tail_sig = paras[-1] if len(paras[-1].split()) <= 4 and not _is_cta_para(paras[-1]) else ""
        core = paras[1:-1] if tail_sig else paras[1:]
        cta_paras = [p for p in core if _is_cta_para(p)]
        prose = [p for p in core if not _is_cta_para(p)]
        cta = _pick_one_cta(cta_paras)
        mid = " ".join(prose).strip()
        paras = [head]
        if mid:
            paras.append(mid)
        if cta:
            paras.append(cta)
        if tail_sig:
            paras.append(tail_sig)
        paras = [p for p in paras if p]
    else:
        # Aunque entren en el tope, nunca dejar 2 CTAs de reunión.
        cta_idxs = [i for i, p in enumerate(paras) if _is_cta_para(p)]
        if len(cta_idxs) > 1:
            keep = cta_idxs[-1]
            for i in cta_idxs:
                if re.search(
                    r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|\d{1,2}:\d{2})\b",
                    paras[i],
                    re.I,
                ):
                    keep = i
                    break
            paras = [p for i, p in enumerate(paras) if i == keep or i not in cta_idxs]
    return "\n\n".join(paras)


def normalize_outbound_email_body(text: str | None) -> str:
    """
    Quita saltos de línea 'blandos' dentro de un párrafo (el modelo a veces
    parte oraciones cada ~40 caracteres). Respeta párrafos separados por línea en blanco
    y deja el saludo corto ('Hola X,') en su propia primera línea.
    Si viene un muro sin párrafos, los arma por tema/oración.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    # Une palabras partidas con guión al final de línea: "prospec-\nción" → "prospección"
    raw = re.sub(r"(\w)-\n+(\w)", r"\1\2", raw)
    raw = ensure_email_topic_paragraphs(raw)

    paragraphs = re.split(r"\n\s*\n+", raw)
    out: list[str] = []
    for para in paragraphs:
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        # Saludo solo: no re-pegarlo al párrafo siguiente (mail ≠ una sola línea).
        if (
            len(lines) == 1
            and _GREETING_RE.match(lines[0])
            and lines[0].endswith(",")
            and len(lines[0]) <= 48
        ):
            out.append(lines[0])
        elif (
            len(lines) >= 2
            and _GREETING_RE.match(lines[0])
            and lines[0].endswith(",")
            and len(lines[0]) <= 48
        ):
            # Soft-wraps bajo el saludo → el resto es el 1º párrafo de contenido.
            rest = _join_soft_lines(lines[1:])
            out.append(lines[0])
            if rest:
                out.append(rest)
        else:
            out.append(_join_soft_lines(lines))
    return "\n\n".join(out)


_LIST_ITEM_RE = re.compile(r"^(?:[•\-*·]|\d{1,2}[.)])\s+")


def _join_soft_lines(lines: list[str]) -> str:
    """Une líneas partidas, pero cada ítem de lista queda en su renglón."""
    merged: list[str] = []
    for ln in lines:
        if merged and not _LIST_ITEM_RE.match(ln) and not _LIST_ITEM_RE.match(merged[-1]):
            merged[-1] = f"{merged[-1]} {ln}"
        else:
            merged.append(ln)
    return "\n".join(merged)


def _msg_direction(msg: Any) -> str:
    if msg is None:
        return ""
    if isinstance(msg, dict):
        return str(msg.get("direction") or "").strip().lower()
    return str(getattr(msg, "direction", "") or "").strip().lower()


def conversation_allows_opening_greeting(history: Sequence[Any] | None) -> bool:
    """
    Saludo solo en frío o en la 1ª réplica tras el primer inbound.
    A partir del 2º mensaje nuestro en el hilo: sin Hola / Buen día.
    """
    msgs = list(history or [])
    saw_inbound = False
    outbound_after_inbound = 0
    for msg in msgs:
        direction = _msg_direction(msg)
        if direction == "inbound":
            saw_inbound = True
        elif direction == "outbound" and saw_inbound:
            outbound_after_inbound += 1
    if not saw_inbound:
        return True
    return outbound_after_inbound == 0


def strip_opening_greeting(text: str | None) -> str:
    """Saca 'Hola X,' / 'Buen día,' del inicio (línea o prefijo)."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    parts = raw.split("\n", 1)
    first = parts[0].strip()
    rest = parts[1].lstrip("\n") if len(parts) > 1 else ""
    match = _OPENING_GREETING_PREFIX.match(first)
    if not match:
        return raw
    remainder = first[match.end() :].strip()
    if remainder:
        if remainder[0].islower():
            remainder = remainder[0].upper() + remainder[1:]
        return f"{remainder}\n{rest}".strip() if rest else remainder
    return rest.strip()


# Latiguillos de réplica inbound: no suman; la respuesta tiene que ir al grano.
# No matchear «Genial Ana!» (nombre pegado): solo acolchado + puntuación/fin de token.
_INBOUND_LATIGUILLO_OPENER = re.compile(
    r"^(?:"
    r"buena\s+pregunta(?:\s+(?:sobre|esa(?:\s+de)?)\s+[^.!?\n]{0,60})?"
    r"|buena\s+esa(?:\s+de\s+[^.!?\n]{0,40})?"
    r"|excelente\s+pregunta"
    r"|muy\s+buena\s+pregunta"
    r"|perfecto"
    r"|genial"
    r"|listo"
    r"|dale"
    r"|ten[eé]s\s+raz[oó]n"
    r"|totalmente(?:\s+de\s+acuerdo)?"
    r"|claro(?:\s+que\s+s[ií])?"
    r"|entendido"
    r"|ok(?:ey)?"
    r"|de\s+una"
    r")(?=\s*[.!,…]|\s*$|\s+(?=[¿¡A-ZÁÉÍÓÚÜÑ]))\s*[.!,…]?\s*",
    re.IGNORECASE,
)


def strip_inbound_latiguillo_opener(text: str | None) -> str:
    """
    Saca coletillas vacías al inicio («Buena pregunta.», «Perfecto,», etc.).
    Conserva un saludo real («Hola Ana,») si estaba.
    Si el mensaje entero ERA solo el latiguillo + CTA corto, deja el resto.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    greet, body = _peel_opening_greeting(raw)
    target = body if greet else raw
    if not target:
        return greet.rstrip(",") + "," if greet else ""

    # Línea propia de latiguillo + cuerpo debajo, o prefijo en la misma línea.
    first, _, rest = target.partition("\n")
    first_st = first.strip()
    rest_st = rest.lstrip("\n").strip()
    m_line = _INBOUND_LATIGUILLO_OPENER.match(first_st)
    line_tail = first_st[m_line.end() :].strip() if m_line else first_st
    if m_line and not line_tail and rest_st:
        cleaned = rest_st
    else:
        m = _INBOUND_LATIGUILLO_OPENER.match(target)
        if m and len(target) > m.end() + 12:
            cleaned = target[m.end() :].strip()
        else:
            cleaned = target

    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    if greet:
        g = greet if greet.endswith(",") else f"{greet.rstrip(',')},"
        return f"{g}\n\n{cleaned}".strip() if cleaned else g
    return cleaned


def apply_opening_greeting_policy(
    text: str | None,
    *,
    allow_greeting: bool,
) -> str:
    raw = strip_inbound_latiguillo_opener(text)
    if not raw:
        return ""
    if allow_greeting:
        return raw
    return strip_opening_greeting(raw)


EmailThreadPhase = Literal["thread_start", "mid_thread", "meeting_confirm"]

_EMAIL_SIGN_OFF_RE = re.compile(
    r"(?:\n\s*){1,3}"
    r"(?:Saludos|Un\s+saludo|Saludo|Cordialmente|Atentamente)\s*,?\s*"
    r"(?:\n+[^\n]+){1,2}\s*$",
    re.IGNORECASE,
)


def resolve_email_thread_phase(
    history: Sequence[Any] | None,
    *,
    is_meeting_confirmation: bool = False,
) -> EmailThreadPhase:
    """
    1) thread_start — frío / 1ª réplica del hilo
    2) mid_thread — ida y vuelta
    3) meeting_confirm — post-agendar (invite enviado / nos vemos)
    """
    if is_meeting_confirmation:
        return "meeting_confirm"
    if conversation_allows_opening_greeting(history):
        return "thread_start"
    return "mid_thread"


def strip_email_sign_off(text: str | None) -> str:
    """Saca firma tipo 'Saludos,\\nNombre' del final (para reaplicar por fase)."""
    raw = (text or "").rstrip()
    if not raw:
        return ""
    return _EMAIL_SIGN_OFF_RE.sub("", raw).rstrip()


def _prospect_first_from_name(name: str | None) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else ""


def apply_email_thread_envelope(
    text: str | None,
    *,
    phase: EmailThreadPhase,
    prospect_name: str | None = None,
    seller_name: str | None = None,
    company_name: str | None = None,
) -> str:
    """
    Sobre de mail por fase del hilo:
    - thread_start: Hola Name, … · Saludos,\\nSeller\\nCompany?
    - mid_thread: sin saludo (si ya hubo ida/vuelta) · Saludos,\\nSeller
    - meeting_confirm: sin Hola formal · Un saludo,\\nSeller
    """
    body = normalize_outbound_email_body(strip_email_sign_off(text))
    if not body:
        return ""

    seller = (seller_name or "").strip()
    seller_first = seller.split()[0] if seller else ""
    prospect_first = _prospect_first_from_name(prospect_name)
    company = (company_name or "").strip()

    if phase == "thread_start":
        body = apply_opening_greeting_policy(body, allow_greeting=True)
        if not _peel_opening_greeting(body)[0] and prospect_first:
            body = normalize_outbound_email_body(f"Hola {prospect_first},\n\n{body}")
        if seller:
            sign = f"Saludos,\n{seller}"
            if company and company.lower() not in seller.lower():
                sign = f"{sign}\n{company}"
            # Firma en renglones propios (no pasar por soft-wrap join).
            return f"{body.rstrip()}\n\n{sign}"
        return body

    if phase == "meeting_confirm":
        body = apply_opening_greeting_policy(body, allow_greeting=False)
        # «Listo X, …» / «Genial» ya cumplen saludo de cierre; no forzar Hola.
        if seller_first:
            return f"{body.rstrip()}\n\nUn saludo,\n{seller_first}"
        return body

    # mid_thread
    body = apply_opening_greeting_policy(body, allow_greeting=False)
    if seller_first:
        return f"{body.rstrip()}\n\nSaludos,\n{seller_first}"
    return body
