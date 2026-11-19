"""Cuándo volver a escribirle a un prospecto que postergó ("más adelante", "en marzo", ...)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

KIND_EXPLICIT = "explicit"
KIND_SOON = "soon"
KIND_NEXT_WEEK = "next_week"
KIND_LATER = "later"
KIND_NOT_NOW = "not_now"
KIND_LLM = "llm"

ASKED_KINDS = frozenset({KIND_EXPLICIT, KIND_SOON, KIND_NEXT_WEEK, KIND_LLM})

DAYS_SOON = 3
DAYS_TOMORROW = 1
DAYS_NEXT_WEEK = 7
DAYS_LATER = 30
DAYS_NOT_NOW = 90

_RESUME_HOUR_UTC = 14

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
}
_NUM_WORDS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "doce": 12,
}
_NUM = r"(\d{1,3}|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|doce)"


@dataclass(frozen=True)
class DeferDecision:
    resume_at: datetime
    kind: str
    label: str | None = None

    @property
    def prospect_asked(self) -> bool:
        return self.kind in ASKED_KINDS


def _fold(text: str | None) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _num(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _NUM_WORDS.get(token)


def _at(d: date) -> datetime:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, _RESUME_HOUR_UTC, 0, 0, tzinfo=UTC)


def _days(now: datetime, n: int) -> datetime:
    return _at(now.date() + timedelta(days=max(1, n)))


def _month_start(year: int, month: int, day: int = 1) -> date:
    return date(year, month, day)


def _next_month_occurrence(today: date, month: int, day: int) -> date:
    year = today.year if month > today.month else today.year + 1
    return _month_start(year, month, day)


def _month_label(t: str, month_name: str) -> tuple[int, str]:
    if re.search(rf"(fin(es|ales)?|final) de {month_name}", t):
        return 25, f"a fin de {month_name}"
    if re.search(rf"mediados de {month_name}", t):
        return 15, f"a mediados de {month_name}"
    if re.search(rf"principios? de {month_name}", t):
        return 2, f"a principios de {month_name}"
    return 2, f"en {month_name}"


def _explicit(t: str, now: datetime) -> tuple[datetime, str] | None:
    today = now.date()

    if "pasado manana" in t:
        return _days(now, 2), "pasado mañana"
    if re.search(r"(?<!la )\bmanana\b", t):
        return _days(now, DAYS_TOMORROW), "mañana"

    m = re.search(rf"\b(?:en|dentro de|de aca a)\s+(?:unos?\s+)?{_NUM}\s+dias?\b", t)
    if m and (n := _num(m.group(1))) and 1 <= n <= 120:
        return _days(now, n), f"en {n} días"
    if re.search(r"\bun par de dias\b", t):
        return _days(now, 2), "en un par de días"

    if re.search(r"(semana que viene|proxima semana|la otra semana)", t):
        return _days(now, 7 - today.weekday()), "la semana que viene"
    m = re.search(rf"\b(?:en|dentro de)\s+{_NUM}\s+semanas?\b", t)
    if m and (n := _num(m.group(1))) and 1 <= n <= 26:
        return _days(now, 7 * n), "en una semana" if n == 1 else f"en {n} semanas"
    if re.search(r"\bun par de semanas\b", t):
        return _days(now, 14), "en un par de semanas"
    if re.search(r"\bunas (pocas )?semanas\b", t):
        return _days(now, 21), "en unas semanas"

    if re.search(r"(a |para )?(fin(es|ales)?|final) de mes", t):
        target = date(today.year, today.month, 26)
        if target <= today:
            nm = (today.replace(day=1) + timedelta(days=32)).replace(day=26)
            target = nm
        return _at(target), "a fin de mes"
    if re.search(r"(mes que viene|proximo mes|el otro mes)", t):
        nm = (today.replace(day=1) + timedelta(days=32)).replace(day=2)
        return _at(nm), "el mes que viene"
    m = re.search(rf"\b(?:en|dentro de)\s+{_NUM}\s+mes(?:es)?\b", t)
    if m and (n := _num(m.group(1))) and 1 <= n <= 18:
        return _days(now, 30 * n), "en un mes" if n == 1 else f"en {n} meses"
    if re.search(r"\bun par de meses\b", t):
        return _days(now, 60), "en un par de meses"

    if re.search(r"(trimestre que viene|proximo trimestre|siguiente trimestre|next quarter)", t):
        q_month = ((today.month - 1) // 3 + 1) * 3 + 1
        year = today.year + (1 if q_month > 12 else 0)
        q_month = q_month - 12 if q_month > 12 else q_month
        return _at(date(year, q_month, 2)), "el trimestre que viene"
    if re.search(r"segundo semestre", t) and today.month < 7:
        return _at(date(today.year, 7, 2)), "en el segundo semestre"
    if re.search(r"(semestre que viene|proximo semestre)", t):
        start = date(today.year, 7, 2) if today.month < 7 else date(today.year + 1, 1, 15)
        return _at(start), "el semestre que viene"

    if re.search(r"(despues|pasad[ao]s?) (de )?(las )?fiestas", t):
        year = today.year + 1 if today.month >= 2 else today.year
        return _at(date(year, 1, 12)), "después de las fiestas"
    if re.search(r"(despues|pasad[ao]) (de )?(las )?vacaciones", t):
        if today.month in (12, 1, 2):
            year = today.year + 1 if today.month == 12 else today.year
            return _at(date(year, 3, 2)), "después de las vacaciones"
        if today.month == 7:
            return _at(date(today.year, 8, 3)), "después de las vacaciones"
    if re.search(r"(despues|pasado) (del )?verano", t):
        return _at(_next_month_occurrence(today, 3, 2)), "después del verano"
    if re.search(r"(ano que viene|proximo ano|el otro ano|next year)", t):
        return _at(date(today.year + 1, 2, 2)), "el año que viene"

    month_hits = [
        (m.start(), name, month)
        for name, month in _MONTHS.items()
        for m in re.finditer(rf"\b{name}\b", t)
    ]
    if month_hits:
        _, name, month = max(month_hits)
        day, label = _month_label(t, name)
        target = _next_month_occurrence(today, month, day)
        if month == today.month and day > today.day:
            target = date(today.year, month, day)
        return _at(target), label

    for name, wd in _WEEKDAYS.items():
        if re.search(rf"\b(el|este|el proximo|para el) {name}\b", t):
            ahead = (wd - today.weekday()) % 7 or 7
            return _days(now, ahead), f"el {name.replace('miercoles', 'miércoles')}"

    return None


_SOON_RE = re.compile(
    r"\b(escribime|hablame|hablamos|escribinos|contactame|volve a escribir(me)?|"
    r"te escribo|charlamos|lo vemos)\b.{0,20}\b(despues|luego|mas tarde|en estos dias|en unos dias)\b"
    r"|\b(en estos dias|en unos dias|en unos dias mas)\b"
    r"|\b(despues|luego) (te|lo) (escribo|vemos|hablamos|aviso)\b"
)
_NEXT_WEEK_RE = re.compile(
    r"\botro dia\b|\besta semana (no|estoy|ando|imposible|complicad)"
    r"|\b(ando|estoy|estamos) (a full|tapad[oa]s?|con mucho laburo|con mucho trabajo|muy ocupad[oa]s?)"
)
_NOT_NOW_RE = re.compile(
    r"\bpor (ahora|el momento) no\b|\bahora no\b|\bno es (el|un buen) momento\b|\bno es prioridad\b"
    r"|\bno es una prioridad\b|\bno estamos (buscando|en eso)\b|\bpor ahora estamos bien\b"
    r"|\bpor ahora (no )?(lo )?necesit"
)
_LATER_RE = re.compile(r"\bmas adelante\b|\botro momento\b|\ben el futuro\b|\bmas para adelante\b")


def _category(t: str) -> str | None:
    if _SOON_RE.search(t):
        return KIND_SOON
    if _NEXT_WEEK_RE.search(t):
        return KIND_NEXT_WEEK
    if _NOT_NOW_RE.search(t):
        return KIND_NOT_NOW
    if _LATER_RE.search(t):
        return KIND_LATER
    if re.search(r"\b(escribime|hablame|hablamos|contactame) (despues|luego|mas tarde)\b", t):
        return KIND_SOON
    if re.search(r"\bmas tarde\b", t):
        return KIND_SOON
    return None


def _parse_iso(iso: str | None) -> datetime | None:
    raw = (iso or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def resolve_defer(
    inbound_text: str | None,
    *,
    llm_iso: str | None = None,
    now: datetime | None = None,
) -> DeferDecision:
    """Fecha de re-contacto según cómo lo dijo el prospecto (sin preguntarle)."""
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    n = n.astimezone(UTC)
    t = _fold(inbound_text)

    hit = _explicit(t, n)
    if hit is not None:
        return DeferDecision(resume_at=hit[0], kind=KIND_EXPLICIT, label=hit[1])

    kind = _category(t)
    if kind == KIND_SOON:
        days = DAYS_TOMORROW if "mas tarde" in t else DAYS_SOON
        return DeferDecision(resume_at=_days(n, days), kind=KIND_SOON)
    if kind == KIND_NEXT_WEEK:
        return DeferDecision(resume_at=_days(n, DAYS_NEXT_WEEK), kind=KIND_NEXT_WEEK)
    if kind == KIND_NOT_NOW:
        return DeferDecision(resume_at=_days(n, DAYS_NOT_NOW), kind=KIND_NOT_NOW)

    parsed = _parse_iso(llm_iso)
    if parsed is not None and parsed > n + timedelta(hours=6):
        return DeferDecision(resume_at=min(parsed, n + timedelta(days=365)), kind=KIND_LLM)
    return DeferDecision(resume_at=_days(n, DAYS_LATER), kind=KIND_LATER)


def build_defer_ack_reply(prospect_name: str | None, inbound_text: str | None) -> str:
    """Respuesta al 'más adelante': confirma sin proponer ni preguntar fecha."""
    first = (prospect_name or "").strip().split(" ")[0] if (prospect_name or "").strip() else ""
    hi = f"Dale {first}" if first else "Dale"
    d = resolve_defer(inbound_text)
    if d.kind == KIND_EXPLICIT and d.label:
        return f"{hi}, te escribo {d.label} entonces. ¡Gracias!"
    if d.kind == KIND_SOON:
        return f"{hi}, te escribo en estos días. ¡Gracias!"
    if d.kind == KIND_NEXT_WEEK:
        return f"{hi}, sin problema. Te escribo otro día. ¡Gracias!"
    thanks = f"Gracias por avisarme, {first}." if first else "Gracias por avisarme."
    return f"{thanks} Te escribo más adelante para ver si ahí tiene más sentido. ¡Éxitos!"


def defer_ack_if_postergado(prospect: object, inbound_text: str | None) -> str | None:
    """Si el último inbound dejó al prospecto en Postergados, la respuesta es el acuse (sin proponer fecha)."""
    if getattr(prospect, "sequence_group", None) != "postergado":
        return None
    return build_defer_ack_reply(getattr(prospect, "name", None), inbound_text)


_NOTE_RE = re.compile(r"\[defer kind=(\w+)(?: label=([^\]]*))?\]")


def defer_note(decision: DeferDecision) -> str:
    label = f" label={decision.label}" if decision.label else ""
    return f"[defer kind={decision.kind}{label}]"


def parse_defer_note(notes: str | None) -> tuple[str | None, str | None]:
    m = _NOTE_RE.search(notes or "")
    if not m:
        return None, None
    return m.group(1), (m.group(2) or None)


def build_recontact_message(
    *,
    prospect_name: str | None,
    product_name: str | None,
    kind: str | None,
) -> str:
    first = (prospect_name or "").strip().split(" ")[0] if (prospect_name or "").strip() else ""
    hola = f"Hola {first}, ¿cómo estás?" if first else "Hola, ¿cómo estás?"
    product = (product_name or "").strip()
    about = f"lo de {product}" if product else "lo que habíamos hablado"
    if kind is None or kind in ASKED_KINDS:
        return (
            f"{hola} Como quedamos, te escribo para retomar {about}.\n\n"
            "¿Te parece si lo vemos en una llamada corta? Si te sirve, te paso un par de horarios."
        )
    return (
        f"{hola} Hace un tiempo me comentaste que no era el momento. "
        f"Te escribo para ver si ahora tiene más sentido retomar {about}.\n\n"
        "Si te sirve, coordinamos una llamada corta."
    )
