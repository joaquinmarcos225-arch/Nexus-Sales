"""Respuestas automáticas de "fuera de la oficina".

No cuentan como respuesta: la secuencia del prospecto sigue. Si dicen cuándo vuelven,
los toques que faltan se corren para que el próximo salga ese día (mismo espaciado).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect

DIRECTION = "auto_reply"
MAX_AWAY_DAYS = 120
_RESUME_HOUR_UTC = 14

_MONTHS = {
    "enero": 1, "january": 1, "febrero": 2, "february": 2, "marzo": 3, "march": 3,
    "abril": 4, "april": 4, "mayo": 5, "may": 5, "junio": 6, "june": 6,
    "julio": 7, "july": 7, "agosto": 8, "august": 8, "septiembre": 9, "setiembre": 9,
    "september": 9, "octubre": 10, "october": 10, "noviembre": 11, "november": 11,
    "diciembre": 12, "december": 12,
}
_WEEKDAYS = {
    "lunes": 0, "monday": 0, "martes": 1, "tuesday": 1, "miercoles": 2, "wednesday": 2,
    "jueves": 3, "thursday": 3, "viernes": 4, "friday": 4,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY_RE = "|".join(_WEEKDAYS)

_SUBJECT_RE = re.compile(
    r"(respuesta automatica|automatic reply|auto[\s-]?reply|out of (the )?office|"
    r"fuera de (la )?oficina|ausencia|autorespuesta|mensaje automatico)"
)
_BODY_RE = re.compile(
    r"(fuera de (la )?oficina|out of (the )?office|respuesta automatica|auto[\s-]?reply|"
    r"mensaje automatico|autoresponder|estoy de vacaciones|me encuentro de vacaciones|"
    r"away from (the )?office|on (annual )?leave|on vacation|acceso limitado (a mi |al )?correo|"
    r"limited access to (my )?e-?mail)"
)

_UNTIL = r"(?:hasta(?: el)?|al|until|till|through)"
_BACK = (
    r"(?:vuelvo|volvere|regreso|regresare|retorno|retomo|me reintegro|me reincorporo|"
    r"estare de vuelta|estare de regreso|de vuelta|de regreso|back on|back|return(?:ing)? on|"
    r"return(?:ing)?)(?: (?:a la oficina|al trabajo|to the office))?(?: el| on)?"
)


def _fold(text: str | None) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def is_out_of_office(subject: str | None, body: str | None) -> bool:
    return bool(_SUBJECT_RE.search(_fold(subject)) or _BODY_RE.search(_fold(body)))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _future(today: date, month: int, day: int, year: int | None = None) -> date | None:
    if year is not None:
        return _safe_date(year if year > 100 else 2000 + year, month, day)
    d = _safe_date(today.year, month, day)
    if d is not None and d < today - timedelta(days=1):
        d = _safe_date(today.year + 1, month, day)
    return d


def _find_date(t: str, today: date) -> date | None:
    """Fecha que aparece justo después de 'hasta' / 'vuelvo' (día-mes, como en LatAm)."""
    m = re.match(rf"(?:(?:{_WEEKDAY_RE}),? )?(\d{{1,2}}) (?:de )?({_MONTH_RE})(?: (?:de )?(\d{{4}}))?", t)
    if m:
        return _future(today, _MONTHS[m.group(2)], int(m.group(1)), int(m.group(3)) if m.group(3) else None)
    m = re.match(rf"(?:(?:{_WEEKDAY_RE}),? )?({_MONTH_RE}) (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (\d{{4}}))?", t)
    if m:
        return _future(today, _MONTHS[m.group(1)], int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    m = re.match(rf"(?:(?:{_WEEKDAY_RE}),? )?(\d{{1,2}})[/.-](\d{{1,2}})(?:[/.-](\d{{2,4}}))?", t)
    if m:
        return _future(today, int(m.group(2)), int(m.group(1)), int(m.group(3)) if m.group(3) else None)
    m = re.match(rf"(?:el |este |next )?({_WEEKDAY_RE})\b", t)
    if m:
        ahead = (_WEEKDAYS[m.group(1)] - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    m = re.match(r"(\d{1,2})\b(?![/.\-:])", t)
    if m:
        day = int(m.group(1))
        d = _safe_date(today.year, today.month, day)
        if d is None or d <= today:
            nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            d = _safe_date(nxt.year, nxt.month, day)
        return d
    return None


def return_date(body: str | None, *, now: datetime | None = None) -> date | None:
    """Primer día en que la persona está de vuelta, o None si no lo dice."""
    now = now or datetime.now(UTC)
    today = now.date()
    t = _fold(body)
    for pattern, day_after in ((_BACK, False), (_UNTIL, True)):
        for m in re.finditer(rf"\b{pattern} ", t):
            d = _find_date(t[m.end():], today)
            if d is None:
                continue
            if day_after:
                d += timedelta(days=1)
            if today < d <= today + timedelta(days=MAX_AWAY_DAYS):
                return d
    return None


def _resume_at(d: date) -> datetime:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, _RESUME_HOUR_UTC, 0, tzinfo=UTC)


def apply_out_of_office(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    body: str,
    now: datetime | None = None,
) -> datetime | None:
    """Corre la secuencia hasta la vuelta y lo deja anotado. Devuelve cuándo sale el próximo toque."""
    from app.services import multichannel_sequence as mseq
    from app.services import prospect_sequence as seq

    now = now or datetime.now(UTC)
    who = prospect.name or prospect.email or "El prospecto"
    back = return_date(body, now=now)
    next_at, _ = seq.compute_next_touch(prospect, campaign)
    if back is None or next_at is None:
        mseq._append_log(
            campaign,
            f"{who} tiene una respuesta automática de fuera de la oficina. "
            "No cuenta como respuesta: la secuencia sigue normal.",
            kind="info",
            channel="email",
        )
        return next_at

    resume = _resume_at(back)
    if next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=UTC)
    if resume > next_at and prospect.sequence_started_at is not None:
        start = prospect.sequence_started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        prospect.sequence_started_at = start + (resume - next_at)
        next_at, _ = seq.compute_next_touch(prospect, campaign)
        prospect.next_touch_at = next_at
    when = next_at.strftime("%d/%m") if next_at else back.strftime("%d/%m")
    mseq._append_log(
        campaign,
        f"{who} está fuera de la oficina y vuelve el {back.strftime('%d/%m')}. "
        f"Nexus sigue con la misma secuencia: el próximo toque sale el {when}.",
        kind="info",
        channel="email",
    )
    db.flush()
    return next_at
