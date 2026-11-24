"""Parseo de horarios propuestos por el prospecto (español)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.services.conversation_intelligence import fold_accents, normalize_inbound_text_for_classification

DayWindow = Literal["any", "morning", "afternoon"]


@dataclass(frozen=True)
class MeetingDayPreference:
    """Día/franja preferida sin hora exacta (ej. «¿jueves?», «viernes a la tarde»)."""

    around: datetime  # UTC, ancla del día pedido
    window: DayWindow = "any"
    same_day_only: bool = True
    label: str = ""

_REPLY_QUOTE_MARKERS = (
    r"\n\s*el\s+[^\n]{2,220}(?:\n[^\n]{0,120})?\s+escribi[oó]\s*:",
    r"\n\s*on\s+[^\n]{2,220}(?:\n[^\n]{0,120})?\s+wrote\s*:",
    r"\n\s*de:\s*.+\n\s*(?:enviado(?:\s+el)?|fecha):",
    r"\n\s*-{2,}\s*(?:original message|mensaje original|forwarded message|mensaje reenviado)",
    r"\n\s*_{3,}\s*",
    r"\n\s*from:\s*.+\n\s*(?:sent|date):",
)


def strip_email_reply_quotes(text: str | None) -> str:
    """Solo el texto nuevo del prospecto, sin el hilo citado de Gmail."""
    if not isinstance(text, str):
        return ""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    raw = "\n" + raw
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


_DURATION_MENTION_RE = re.compile(r"\b\d{1,3}\s*(?:min(?:utos)?|mins?)\b|\b\d{1,3}\s*'")
_DAY_NUMBER_RE = re.compile(
    r"\b(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+(?:\d{1,2})(?:/\d{1,2})?\b(?!\s*(?:[:.]\d|hs|hrs|h\b|am|pm))"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
)


_TIME_ANCHOR_RE = re.compile(
    r"\ba\s+las\b"
    r"|\b(?:[01]?\d|2[0-3])\s*(?:[:.]\d{2})\b"
    r"|\b(?:[01]?\d|2[0-3])\s*(?:hs|hrs|h|am|pm)\b"
    r"|\b(?:[01]?\d|2[0-3])(?:hs|hrs|h|am|pm)\b",
    re.I,
)


def _has_explicit_time_anchor(norm: str) -> bool:
    """Hora de reunión con marcador claro (no un dígito suelto tipo teléfono o CUIT)."""
    return bool(_TIME_ANCHOR_RE.search(norm))


def inbound_is_contact_or_data_only(text: str | None) -> bool:
    """Teléfono, CUIT u otro dato: solo dígitos y separadores."""
    source = strip_email_reply_quotes(text or "").strip()
    if not source:
        return False
    compact = re.sub(r"[\s\-\+\.\,\;\(\)]+", "", source)
    if not compact.isdigit():
        return False
    return len(compact) >= 6


def inbound_lacks_scheduling_content(text: str | None) -> bool:
    """Mensaje sin evidencia de coordinar reunión (números sueltos o datos de contacto)."""
    source = strip_email_reply_quotes(text or "").strip()
    if not source:
        return True
    if inbound_is_contact_or_data_only(source):
        return True
    folded = fold_accents(normalize_inbound_text_for_classification(source))
    compact = re.sub(r"[\s\-\+\.\,\;\(\)]+", "", folded)
    if compact.isdigit() and len(compact) <= 3:
        return True
    return False


def _parse_hour(norm: str) -> int | None:
    # «a 15 minutos» es una duración, no las 15:00.
    norm = _DURATION_MENTION_RE.sub(" ", norm)
    norm = re.sub(r"\b(\d{1,2})\s+y\s+media\b", r"\1:30", norm)
    norm = re.sub(r"\b(\d{1,2})\s+y\s+cuarto\b", r"\1:15", norm)
    # «el lunes 19 a las 9», «el 16/10 a las 15»: el número de día no es la hora.
    if re.search(r"\ba las\b|\d\s*(?:hs|hrs|h)\b|\d[:.]\d{2}", norm):
        norm = _DAY_NUMBER_RE.sub(" ", norm)
    patterns = (
        (2, r"\b(\d{1,2})\s*(?:[:.]\s*(\d{2}))?\s*(hs|hrs|h|am|pm)\b"),
        (2, r"\b(\d{1,2})(hs|hrs|h)\b"),
        (3, r"\ba las\s+(\d{1,2})(?:\s*(?:[:.]\s*(\d{2}))?\s*(hs|hrs|h|am|pm)?)?\b"),
        (1, r"\b(\d{1,2})\s*(?:[:.]\s*(\d{2}))\s*(?:hs|hrs|h|am|pm)?\b"),
    )
    best: tuple[int, int, re.Match[str]] | None = None
    for priority, pattern in patterns:
        for m in re.finditer(pattern, norm):
            pos = m.start()
            end = m.end()
            if best is None or end > best[0] or (end == best[0] and priority > best[1]):
                best = (end, priority, m)
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


def _explicit_next_occurrence_marker(norm: str) -> bool:
    """«martes que viene», «próximo jueves», «la semana que viene»."""
    if "proxima semana" in norm or "semana que viene" in norm:
        return True
    if re.search(
        r"\b(?:proxima|proximo|siguiente)\s+(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
        norm,
    ):
        return True
    if re.search(
        r"\b(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+"
        r"(?:que\s+viene|proximo|proxima|siguiente)\b",
        norm,
    ):
        return True
    return False


def _resolve_day_anchor(norm: str, text: str | None, base: datetime) -> datetime:
    if "pasado manana" in norm or "pasado mañana" in (text or "").lower():
        return base + timedelta(days=2)
    if re.search(r"\bhoy\b", norm) or "dia de hoy" in norm:
        return base
    if "manana" in norm or "mañana" in (text or "").lower():
        return base + timedelta(days=1)
    for name, wd in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", norm):
            day = _next_weekday(base, wd)
            # Hoy es martes y dice «martes que viene» → próxima semana, no hoy.
            if day.date() == base.date() and _explicit_next_occurrence_marker(norm):
                day = day + timedelta(days=7)
            return day
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


def _detect_day_window(norm: str, text: str | None) -> DayWindow | None:
    """Franja vaga (mañana/tarde) sin clock time. None si no hay franja."""
    raw = (text or "").lower()
    # Evitar «buenas tardes» / «más tarde»: solo franjas de agenda explícitas.
    if (
        "por la tarde" in norm
        or "a la tarde" in norm
        or "de la tarde" in norm
        or re.search(r"\b(esta|alguna|cualquier)\s+tarde\b", norm)
    ):
        return "afternoon"
    if (
        "por la manana" in norm
        or "a la manana" in norm
        or "de la manana" in norm
        or "por la mañana" in raw
        or "a la mañana" in raw
        or "de la mañana" in raw
        or re.search(r"\b(esta|alguna|cualquier)\s+manana\b", norm)
    ):
        return "morning"
    return None


def parse_meeting_day_preference(
    text: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> MeetingDayPreference | None:
    """
    Día o franja sin hora exacta → preferencia para ofrecer huecos reales de Calendar.
    Si hay clock time bookable, devuelve None (eso va a auto-book).
    """
    source = strip_email_reply_quotes(text)
    norm = fold_accents(normalize_inbound_text_for_classification(source))
    if not norm:
        return None
    if _parse_hour(norm) is not None:
        return None

    window = _detect_day_window(norm, source) or "any"
    has_day = _has_explicit_day(norm, source)
    if not has_day and window == "any":
        return None

    try:
        tz = ZoneInfo(timezone)
    except Exception:
        try:
            tz = ZoneInfo("America/Argentina/Buenos_Aires")
        except Exception:
            return None

    ref = (now or datetime.now(UTC)).astimezone(tz)
    base = ref.replace(second=0, microsecond=0)

    if has_day:
        target = _resolve_day_anchor(norm, text, base)
        # «próxima semana» sin weekday: mirar esa semana, no un solo día.
        soft_week = "proxima semana" in norm or "semana que viene" in norm
        weekday_named = any(re.search(rf"\b{name}\b", norm) for name in _WEEKDAYS)
        same_day_only = not soft_week or weekday_named
        if soft_week and not weekday_named:
            target = base + timedelta(days=7)
            same_day_only = False
        label_local = target.astimezone(tz)
        days = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
        label = days[label_local.weekday()]
        if window == "afternoon":
            label = f"{label} a la tarde"
        elif window == "morning":
            label = f"{label} a la mañana"
        around = target.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC)
        return MeetingDayPreference(
            around=around,
            window=window,
            same_day_only=same_day_only,
            label=label,
        )

    # Solo franja («alguna tarde»): anclar en el próximo día hábil.
    around_local = base + timedelta(days=1)
    around = around_local.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC)
    label = "a la tarde" if window == "afternoon" else "a la mañana"
    return MeetingDayPreference(
        around=around,
        window=window,
        same_day_only=False,
        label=label,
    )


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

    from app.services.prospect_timezone import timezone_hint_from_text

    hinted = timezone_hint_from_text(source)
    if hinted:
        timezone = hinted

    try:
        tz = ZoneInfo(timezone)
    except Exception:
        # Alias viejos / imagen sin zoneinfo del sistema → fallback AR.
        try:
            tz = ZoneInfo("America/Argentina/Buenos_Aires")
        except Exception:
            return None
    ref = (now or datetime.now(UTC)).astimezone(tz)
    base = ref.replace(second=0, microsecond=0)

    target = _resolve_day_anchor(norm, text, base)

    minutes = _parse_hour(norm)
    if minutes is None:
        # Franjas vagas («por la tarde») no inventan hora: van a day preference + freeBusy.
        return None

    hour, minute = divmod(minutes, 60)
    explicit_day = _has_explicit_day(norm, text)
    if (
        not explicit_day
        and not _has_explicit_time_anchor(norm)
        and not (context_meeting_at is not None and _inherits_context_day(norm))
    ):
        return None

    if not explicit_day and context_meeting_at is not None and _inherits_context_day(norm):
        ctx = context_meeting_at.astimezone(tz)
        candidate = ctx.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base + timedelta(hours=1):
            return None
        return candidate.astimezone(UTC)

    candidate = target.replace(hour=hour, minute=minute)
    min_notice = base + timedelta(hours=1)
    weekday_named = any(re.search(rf"\b{name}\b", norm) for name in _WEEKDAYS)
    if candidate <= base:
        if "proxima semana" in norm or "semana que viene" in norm:
            candidate += timedelta(days=7)
        elif re.search(r"\bhoy\b", norm) or "dia de hoy" in norm:
            # «Hoy a las 10» con la hora ya pasada: no inventar otro día.
            return None
        elif target.date() == base.date() and weekday_named:
            # «Martes a las 10» un martes a la tarde → próximo martes (no fallar en silencio).
            candidate += timedelta(days=7)
        elif target.date() == base.date():
            return None
        else:
            candidate += timedelta(days=7)
    elif candidate < min_notice:
        return None
    return candidate.astimezone(UTC)


_SLOT_SPLIT_RE = re.compile(
    r"\s+(?:o|u)\s+"
    r"(?=el\s+|l[au]\s+|los\s+|las\s+|"
    r"lunes|martes|miercoles|miércoles|jueves|viernes|s[aá]bado|domingo|"
    r"mañana|manana|hoy|pasado)",
    re.I,
)


def parse_meeting_slots(
    text: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
    context_meeting_at: datetime | None = None,
) -> list[datetime]:
    """Todos los horarios que el prospecto ofreció (ej. 'lunes 11 o martes 15')."""
    source = strip_email_reply_quotes(text)
    if not source.strip():
        return []
    parts = [p.strip() for p in _SLOT_SPLIT_RE.split(source) if p and p.strip()]
    if len(parts) < 2:
        parts = [source]
    out: list[datetime] = []
    seen: set[str] = set()
    for part in parts:
        slot = parse_meeting_slot(
            part,
            now=now,
            timezone=timezone,
            context_meeting_at=context_meeting_at,
        )
        if slot is None:
            continue
        key = slot.isoformat()
        if key in seen:
            continue
        seen.add(key)
        out.append(slot)
    if not out:
        slot = parse_meeting_slot(
            source,
            now=now,
            timezone=timezone,
            context_meeting_at=context_meeting_at,
        )
        if slot is not None:
            out.append(slot)
    return out
