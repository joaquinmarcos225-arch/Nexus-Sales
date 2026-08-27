"""Parseo de horarios propuestos por el prospecto (español)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.conversation_intelligence import fold_accents, normalize_inbound_text_for_classification

_REPLY_QUOTE_MARKERS = (
    r"\n\s*el\s+.{8,220}\s+escribi[oó]:\s*",
    r"\n\s*on\s+.{8,220}\s+wrote:\s*",
    r"\n\s*de:\s*.+\n\s*enviado(?:\s+el)?:",
    r"\n\s*-{3,}\s*original message",
    r"\n\s*_{3,}\s*",
    r"\n\s*from:\s*.+\n\s*sent:",
)


def strip_email_reply_quotes(text: str | None) -> str:
    """Solo el texto nuevo del prospecto, sin el hilo citado de Gmail."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    cut = len(raw)
    for pattern in _REPLY_QUOTE_MARKERS:
        m = re.search(pattern, raw, flags=re.IGNORECASE)
        if m and m.start() < cut:
            cut = m.start()
    head = raw[:cut]
    lines: list[str] = []
    for line in head.split("\n"):
        if line.strip().startswith(">"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}


def _next_weekday(anchor: datetime, weekday: int) -> datetime:
    """Mismo día de la semana; si hoy ya es ese día, usa hoy (no la semana siguiente)."""
    days_ahead = (weekday - anchor.weekday()) % 7
    return anchor + timedelta(days=days_ahead)


def _parse_hour(norm: str) -> int | None:
    patterns = (
        (2, r"\b(\d{1,2})\s*(?::\s*(\d{2}))?\s*(hs|hrs|h|am|pm)\b"),
        (2, r"\b(\d{1,2})(hs|hrs|h)\b"),
        (1, r"\ba las\s+(\d{1,2})(?:\s*(?::\s*(\d{2}))?\s*(hs|hrs|h|am|pm)?)?\b"),
        (1, r"\b(\d{1,2})\s*(?::\s*(\d{2}))?\s*(hs|hrs|h|am|pm)?\b"),
    )
    best: tuple[int, int, re.Match[str]] | None = None
    for priority, pattern in patterns:
        for m in re.finditer(pattern, norm):
            pos = m.start()
            if best is None or priority > best[0] or (priority == best[0] and pos < best[1]):
                best = (priority, pos, m)
    if best is None:
        return None
    m = best[2]
    hour = int(m.group(1))
    minute = 0
    suffix = ""
    if m.lastindex and m.lastindex >= 2:
        g2 = (m.group(2) or "").strip().lower()
        if g2 in ("hs", "hrs", "h", "am", "pm"):
            suffix = g2
        elif g2.isdigit():
            minute = int(g2)
    if m.lastindex and m.lastindex >= 3 and m.group(3):
        suffix = str(m.group(3)).lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if 7 <= hour <= 9 and "tarde" in norm and hour < 12:
        hour += 12
    if hour < 7 or hour > 21:
        return None
    return hour * 60 + minute


def prospect_proposed_meeting_slot(text: str | None) -> bool:
    """True si el mensaje incluye día u hora concreta para reunión."""
    return parse_meeting_slot(text) is not None


def _has_explicit_day(norm: str, text: str | None) -> bool:
    if "pasado manana" in norm or "pasado mañana" in (text or "").lower():
        return True
    if re.search(r"\bhoy\b", norm) or "dia de hoy" in norm:
        return True
    if "manana" in norm or "mañana" in (text or "").lower():
        return True
    if "proxima semana" in norm or "semana que viene" in norm:
        return True
    return any(re.search(rf"\b{name}\b", norm) for name in _WEEKDAYS)


def _resolve_day_anchor(norm: str, text: str | None, base: datetime) -> datetime:
    if "pasado manana" in norm or "pasado mañana" in (text or "").lower():
        return base + timedelta(days=2)
    if re.search(r"\bhoy\b", norm) or "dia de hoy" in norm:
        return base
    if "manana" in norm or "mañana" in (text or "").lower():
        return base + timedelta(days=1)
    for name, wd in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", norm):
            return _next_weekday(base, wd)
    return base


def _is_reschedule_intent(norm: str) -> bool:
    keys = (
        "pasar",
        "cambiar",
        "mover",
        "reprogram",
        "reagend",
        "otro horario",
        "no puedo",
        "al final",
        "en su lugar",
        "mejor ",
        "esa hr",
        "esa hora",
    )
    return any(k in norm for k in keys)


def _is_slot_acceptance_intent(norm: str) -> bool:
    """Acepta un horario ofrecido sin repetir el día (ej. «a las 14:30 puedo»)."""
    keys = (
        "puedo",
        "me agendas",
        "me agend",
        "agendame",
        "agendá",
        "esta bien",
        "está bien",
        "agendame porfavor",
        "agendá porfavor",
        "me viene bien",
        "me sirve",
        "dale",
        "perfecto",
        "confirmo",
        "de acuerdo",
        "ese horario",
        "esa hora",
        "a esa hora",
        "ok",
        "sip",
        "si ",
        "sí ",
    )
    return any(k in norm for k in keys)


def _inherits_context_day(norm: str) -> bool:
    return _is_reschedule_intent(norm) or _is_slot_acceptance_intent(norm)


def infer_meeting_day_context(
    text: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> datetime | None:
    """
    Día explícito del mensaje (viernes, mañana, etc.) sin exigir hora.
    Sirve para heredar el día cuando el prospecto solo confirma la hora.
    """
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    if not norm or not _has_explicit_day(norm, source):
        return None

    tz = ZoneInfo(timezone)
    ref = (now or datetime.now(UTC)).astimezone(tz)
    base = ref.replace(second=0, microsecond=0)
    target = _resolve_day_anchor(norm, text, base)
    return target.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC)


def inbound_has_explicit_day_anchor(text: str | None) -> bool:
    """True si el mensaje actual fija el día (hoy, mañana, viernes, etc.)."""
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    return bool(norm) and _has_explicit_day(norm, source)


def inbound_is_time_only_acceptance(text: str | None) -> bool:
    """True si el prospecto solo confirma hora (sin repetir el día)."""
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    if not norm:
        return False
    return _inherits_context_day(norm) and not _has_explicit_day(norm, source)


def parse_meeting_duration_minutes(text: str | None, *, default: int = 30) -> int:
    """
    Duración pedida por el prospecto (ej. «puedo 15 min» → 15).
    Si no menciona duración, devuelve default (30).
    Preferí el destino en «de 30 a 15 min».
    """
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    if not norm:
        return default

    de_a = _parse_duration_from_to(norm)
    if de_a is not None:
        return de_a

    if re.search(r"\bmedia\s+hora\b", norm):
        return 30
    if re.search(r"\bhora\s+y\s+media\b", norm):
        return 90
    if re.search(r"\b(?:una|1)\s+hora\b", norm):
        return 60

    best: int | None = None
    for m in re.finditer(r"\b(\d{1,3})\s*(?:min(?:utos)?|mins?)\b", norm):
        n = int(m.group(1))
        if 5 <= n <= 240:
            best = n
            break

    if best is None:
        m = re.search(r"\b(\d{1,3})\s*'", norm)
        if m:
            n = int(m.group(1))
            if 5 <= n <= 240:
                best = n

    if best is None:
        m = re.search(
            r"\b(?:reunion|llamada|demo|call|meet(?:ing)?)\s+(?:de|por)\s+(\d{1,3})\b",
            norm,
        )
        if m:
            n = int(m.group(1))
            if 5 <= n <= 240:
                best = n

    if best is None:
        m = re.search(r"\b(?:de|por)\s+(\d{1,3})\s*(?:min(?:utos)?|mins?)?\b", norm)
        if m:
            n = int(m.group(1))
            if n in (10, 15, 20, 25, 30, 45, 60, 90, 120):
                best = n

    if best is not None:
        return best

    m = re.search(r"\b(?:puedo|tengo|disponible|nos\s+toma)\s+(\d{1,2})\b", norm)
    if m:
        n = int(m.group(1))
        if n in (10, 15, 20, 25, 30, 45, 60, 90):
            return n

    return default


_DURATION_UNIT = r"(?:min(?:utos)?|mins?|'\s*)?"
_HOUR_AS_MIN = {
    "media hora": 30,
    "hora y media": 90,
    "una hora": 60,
    "1 hora": 60,
}


def _parse_duration_token(token: str) -> int | None:
    t = (token or "").strip()
    if not t:
        return None
    for phrase, mins in _HOUR_AS_MIN.items():
        if phrase in t:
            return mins
    m = re.search(r"(\d{1,3})", t)
    if not m:
        return None
    n = int(m.group(1))
    if 5 <= n <= 240:
        return n
    return None


def _parse_duration_from_to(norm: str) -> int | None:
    """«de 30 a 15 min» / «de media hora a 15» → duración destino."""
    m = re.search(
        rf"\bde\s+(.{{1,24}}?)\s+a\s+(\d{{1,3}}\s*{_DURATION_UNIT}|media\s+hora|hora\s+y\s+media|una\s+hora)",
        norm,
    )
    if not m:
        return None
    target = _parse_duration_token(m.group(2))
    if target is not None:
        return target
    return None


def inbound_is_duration_only_change(text: str | None) -> int | None:
    """
    Si el mensaje solo pide cambiar la duración de una reunión ya agendada
    (sin proponer un horario nuevo), devuelve los minutos destino.
    Ej.: «cambiar de 30 a 15 min», «Si, solamente a 15min».
    """
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    if not norm:
        return None

    # Si hay día/hora de agenda concreto, lo maneja auto-book (no es “solo duración”).
    if parse_meeting_slot(source) is not None:
        return None

    target = _parse_duration_from_to(norm)
    if target is None:
        # «pasarla a 15 min», «que sea de 15 minutos», «solo 15 min»
        m = re.search(
            rf"\b(?:a|de|en)\s+(\d{{1,3}})\s*(?:min(?:utos)?|mins?|')\b",
            norm,
        )
        if m:
            n = int(m.group(1))
            if 5 <= n <= 240:
                target = n
        if target is None and re.search(r"\bmedia\s+hora\b", norm):
            target = 30
        if target is None and re.search(r"\bhora\s+y\s+media\b", norm):
            target = 90
        if target is None and re.search(r"\b(?:una|1)\s+hora\b", norm):
            target = 60

    if target is None:
        return None

    change_keys = (
        "cambiar",
        "cambio",
        "pasarla",
        "pasarlo",
        "pasar a",
        "pasar de",
        "acortar",
        "reducir",
        "acorta",
        "solamente",
        "solo cambiar",
        "solo la duracion",
        "solo de",
        "en vez de",
        "en lugar de",
        "que sea de",
        "que dure",
        "duracion",
    )
    has_change = any(k in norm for k in change_keys)
    has_confirm = bool(
        re.search(r"\b(si|sip|dale|ok|okay|perfecto|confirmo|de acuerdo)\b", norm)
    )
    if not (has_change or (has_confirm and ("min" in norm or "hora" in norm))):
        return None

    return target


def parse_meeting_slot(
    text: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
    context_meeting_at: datetime | None = None,
) -> datetime | None:
    """
    Interpreta frases como 'martes 15 hs', 'jueves a las 10', 'mañana por la tarde'.
    Devuelve datetime aware en la zona indicada.
    """
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    if not norm:
        return None

    tz = ZoneInfo(timezone)
    ref = (now or datetime.now(UTC)).astimezone(tz)
    base = ref.replace(second=0, microsecond=0)

    target = _resolve_day_anchor(norm, text, base)

    minutes = _parse_hour(norm)
    if minutes is None:
        if "por la tarde" in norm or "tarde" in norm:
            minutes = 15 * 60
        elif "por la manana" in norm or "mañana por la" in (text or "").lower():
            minutes = 10 * 60
        else:
            return None

    hour, minute = divmod(minutes, 60)
    explicit_day = _has_explicit_day(norm, text)

    if not explicit_day and context_meeting_at is not None and _inherits_context_day(norm):
        ctx = context_meeting_at.astimezone(tz)
        candidate = ctx.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base + timedelta(hours=1):
            return None
        return candidate.astimezone(UTC)

    candidate = target.replace(hour=hour, minute=minute)
    min_notice = base + timedelta(hours=1)
    if candidate <= base:
        if "proxima semana" in norm or "semana que viene" in norm:
            candidate += timedelta(days=7)
        elif re.search(r"\bhoy\b", norm) or "dia de hoy" in norm:
            return None
        elif target.date() == base.date():
            return None
        else:
            candidate += timedelta(days=7)
    elif candidate < min_notice:
        return None
    return candidate.astimezone(UTC)
