"""
Aprende qué forma de responder a un inbound hace avanzar la conversación, por empresa,
canal y situación (preguntó precio, puso una objeción, pidió info…).

Cada respuesta nuestra a un mensaje del prospecto es una muestra. Resultado:
- avanzó: después se agendó reunión o el prospecto respondió con intención de agendar.
- respondió: volvió a escribir (sin rechazo).
Rasgos de la respuesta: cierre, largo, cantidad de preguntas, dato concreto, si nombra la empresa.

A la IA se le pasa la comparación y la «forma» de las respuestas que funcionaron,
nunca el texto de otras conversaciones: así aprende el criterio sin copiar frases.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services import outreach_metrics as om

LOOKBACK_DAYS = 180
MAX_MESSAGES = 8000
MIN_REPLY_CHARS = 20
MEETING_WINDOW = timedelta(days=30)
MIN_PER_VALUE = 8
MIN_POSITIVES = 3
MIN_LIFT = 1.3
MAX_SHAPES = 3
CACHE_TTL_S = 600.0
ALL = "todas"

_REPLY_SENDERS = ("ai", "user")

SITUATION_LABELS = {
    "precio": "pregunta por el precio",
    "pregunta": "hace una pregunta",
    "pide_info": "pide más información",
    "objecion": "pone una objeción",
    "quiere_reunion": "quiere agendar",
    "timing": "pide tiempo",
    "comentario": "comenta algo general",
    ALL: "en cualquier situación",
}
TRAIT_LABELS = {
    "cierre": "Cierre",
    "largo": "Largo",
    "preguntas": "Preguntas",
    "dato": "Dato o ejemplo concreto",
    "empresa": "Nombrar la empresa",
}
VALUE_LABELS = {
    ("cierre", "link_agenda"): "cerrar con el link de agenda",
    ("cierre", "horarios"): "proponer días u horarios concretos",
    ("cierre", "pregunta"): "cerrar con una pregunta",
    ("cierre", "afirmacion"): "cerrar sin pregunta ni propuesta",
    ("largo", "corto"): "respuestas cortas (hasta 40 palabras)",
    ("largo", "medio"): "respuestas medianas (41 a 80 palabras)",
    ("largo", "largo"): "respuestas largas (más de 80 palabras)",
    ("preguntas", "0"): "sin preguntas",
    ("preguntas", "1"): "una sola pregunta",
    ("preguntas", "2+"): "dos o más preguntas",
    ("dato", "si"): "incluir un dato o ejemplo concreto",
    ("dato", "no"): "sin datos ni ejemplos concretos",
    ("empresa", "si"): "nombrar la empresa del prospecto",
    ("empresa", "no"): "sin nombrar la empresa",
}

_PRICE_RE = re.compile(r"(?i)\b(precio|precios|cu[aá]nto (sale|cuesta|cobran|vale)|costo|costos|tarifa|presupuesto|planes?)\b")
_LINK_RE = re.compile(r"(?i)(https?://|www\.|calendly|cal\.com|meet\.google|zoom\.us)")
_SLOT_RE = re.compile(
    r"(?i)\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|ma[nñ]ana a las|"
    r"\d{1,2}(:\d{2})?\s*(hs|h|am|pm)\b|\d{1,2}:\d{2})"
)
_DATA_RE = re.compile(r"(?i)(\d+\s*%|\d+\s*x\b|\b\d{2,}\b|por ejemplo|un caso|clientes como|casos como)")
_WORD_RE = re.compile(r"\S+")


def situation_of(inbound_text: str | None) -> str | None:
    """Situación del mensaje del prospecto (None = rechazo: no se aprende de eso)."""
    from app.services import conversation_intelligence as ci

    text = ci.normalize_inbound_text_for_classification(inbound_text or "").strip()
    if not text:
        return None
    sig = ci.build_signals_from_keywords(text, None)
    if sig.objection_type == "not_interested":
        return None
    if sig.explicit_meeting_commitment or sig.prospect_wants_meeting:
        return "quiere_reunion"
    if sig.prospect_timing_hold or sig.objection_type == "timing":
        return "timing"
    if _PRICE_RE.search(text):
        return "precio"
    if sig.objection_type == "send_info":
        return "pide_info"
    if sig.objection_type in ("no_time", "competitor", "not_priority", "other"):
        return "objecion"
    if sig.asks_concrete_questions or "?" in text:
        return "pregunta"
    return "comentario"


def reply_traits(reply: str, *, company_name: str | None = None) -> dict[str, str]:
    text = (reply or "").strip()
    words = len(_WORD_RE.findall(text))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if _LINK_RE.search(text):
        cierre = "link_agenda"
    elif _SLOT_RE.search(text):
        cierre = "horarios"
    elif last.rstrip().endswith("?"):
        cierre = "pregunta"
    else:
        cierre = "afirmacion"
    q = text.count("?")
    company = (company_name or "").strip()
    return {
        "cierre": cierre,
        "largo": "corto" if words <= 40 else ("medio" if words <= 80 else "largo"),
        "preguntas": "0" if q == 0 else ("1" if q == 1 else "2+"),
        "dato": "si" if _DATA_RE.search(text) else "no",
        "empresa": "si" if len(company) >= 3 and company.lower() in text.lower() else "no",
    }


def shape_description(traits: dict[str, str], words: int) -> str:
    largo = {"corto": "corta", "medio": "mediana", "largo": "larga"}.get(traits.get("largo", ""), "")
    parts = [f"respuesta {largo} ({words} palabras)"]
    parts.append("con un dato o ejemplo concreto" if traits.get("dato") == "si" else "sin datos")
    if traits.get("empresa") == "si":
        parts.append("nombrando su empresa")
    parts.append(VALUE_LABELS.get(("preguntas", traits.get("preguntas", "")), ""))
    parts.append(VALUE_LABELS.get(("cierre", traits.get("cierre", "")), ""))
    return ", ".join(p for p in parts if p)


@dataclass
class ReplySample:
    channel: str
    situation: str
    traits: dict[str, str]
    words: int
    replied: bool
    advanced: bool


@dataclass
class ReplyInsight:
    trait: str
    value: str
    other_value: str
    n: int
    other_n: int
    advance_rate: float
    other_advance_rate: float
    reply_rate: float
    other_reply_rate: float
    lift: float


@dataclass
class ReplyLearning:
    channel: str
    situation: str
    samples: int = 0
    advanced: int = 0
    insights: list[ReplyInsight] = field(default_factory=list)
    shapes: list[str] = field(default_factory=list)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def samples_from_thread(
    messages: list[Any],
    *,
    company_name: str | None,
    meetings_at: list[datetime],
) -> list[ReplySample]:
    """Muestras de un hilo (mensajes ordenados por fecha)."""
    from app.services import conversation_intelligence as ci

    meetings = sorted(m for m in (_aware(x) for x in meetings_at) if m is not None)
    out: list[ReplySample] = []
    for i, m in enumerate(messages):
        if m.direction != "inbound":
            continue
        situation = situation_of(m.message)
        if situation is None:
            continue
        reply = None
        reply_idx = -1
        for j in range(i + 1, len(messages)):
            r = messages[j]
            if r.direction == "inbound":
                break
            if r.direction == "outbound" and r.sender_type in _REPLY_SENDERS:
                if len((r.message or "").strip()) >= MIN_REPLY_CHARS:
                    reply, reply_idx = r, j
                break
        if reply is None:
            continue
        at = _aware(reply.created_at)
        if at is None or any(mt <= at for mt in meetings):
            continue
        nxt = next((x for x in messages[reply_idx + 1 :] if x.direction == "inbound"), None)
        advanced = any(at < mt <= at + MEETING_WINDOW for mt in meetings)
        replied = False
        if nxt is not None:
            text = ci.normalize_inbound_text_for_classification(nxt.message or "")
            sig = ci.build_signals_from_keywords(text, None)
            if sig.objection_type != "not_interested":
                replied = True
                advanced = advanced or bool(sig.explicit_meeting_commitment or sig.prospect_wants_meeting)
        body = (reply.message or "").strip()
        out.append(
            ReplySample(
                channel=(reply.channel or m.channel or "email").lower(),
                situation=situation,
                traits=reply_traits(body, company_name=company_name),
                words=len(_WORD_RE.findall(body)),
                replied=replied,
                advanced=advanced,
            )
        )
    return out


def collect_samples(db: Session, company_id: int) -> list[ReplySample]:
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    rows = db.execute(
        select(OutreachMessage, Prospect.company_name)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.is_testing.is_(False),
            OutreachMessage.created_at >= since,
            om.exclude_testing_commercial_prospects(),
        )
        .order_by(OutreachMessage.prospect_id, OutreachMessage.created_at, OutreachMessage.id)
        .limit(MAX_MESSAGES)
    ).all()
    meetings: dict[int, list[datetime]] = defaultdict(list)
    for pid, created in db.execute(
        select(Meeting.prospect_id, Meeting.created_at).where(Meeting.company_id == company_id)
    ).all():
        meetings[int(pid)].append(created)
    threads: dict[int, list[Any]] = defaultdict(list)
    companies: dict[int, str | None] = {}
    for msg, company_name in rows:
        threads[int(msg.prospect_id)].append(msg)
        companies[int(msg.prospect_id)] = company_name
    out: list[ReplySample] = []
    for pid, msgs in threads.items():
        out.extend(samples_from_thread(msgs, company_name=companies.get(pid), meetings_at=meetings.get(pid, [])))
    return out


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def _insights(samples: list[ReplySample]) -> list[ReplyInsight]:
    out: list[ReplyInsight] = []
    for trait in TRAIT_LABELS:
        groups: dict[str, list[int]] = {}
        for s in samples:
            g = groups.setdefault(s.traits.get(trait, ""), [0, 0, 0])
            g[0] += 1
            g[1] += int(s.advanced)
            g[2] += int(s.replied)
        eligible = [(v, g) for v, g in groups.items() if v and g[0] >= MIN_PER_VALUE]
        if len(eligible) < 2:
            continue
        eligible.sort(key=lambda vg: -((vg[1][1] + 1) / (vg[1][0] + 2)))
        (bv, b), (ov, o) = eligible[0], eligible[1]
        if b[1] < MIN_POSITIVES:
            continue
        lift = ((b[1] + 1) / (b[0] + 2)) / ((o[1] + 1) / (o[0] + 2))
        if lift < MIN_LIFT:
            continue
        out.append(
            ReplyInsight(
                trait=trait, value=bv, other_value=ov, n=b[0], other_n=o[0],
                advance_rate=_rate(b[1], b[0]), other_advance_rate=_rate(o[1], o[0]),
                reply_rate=_rate(b[2], b[0]), other_reply_rate=_rate(o[2], o[0]),
                lift=round(lift, 2),
            )
        )
    out.sort(key=lambda i: -i.lift)
    return out


def _shapes(samples: list[ReplySample]) -> list[str]:
    seen: list[str] = []
    for s in samples:
        if not s.advanced:
            continue
        d = shape_description(s.traits, s.words)
        key = re.sub(r"\(\d+ palabras\)", "", d)
        if all(re.sub(r"\(\d+ palabras\)", "", x) != key for x in seen):
            seen.append(d)
        if len(seen) >= MAX_SHAPES:
            break
    return seen


def learn(samples: list[ReplySample]) -> dict[tuple[str, str], ReplyLearning]:
    buckets: dict[tuple[str, str], list[ReplySample]] = defaultdict(list)
    for s in samples:
        buckets[(s.channel, s.situation)].append(s)
        buckets[(s.channel, ALL)].append(s)
    out: dict[tuple[str, str], ReplyLearning] = {}
    for (channel, situation), items in buckets.items():
        out[(channel, situation)] = ReplyLearning(
            channel=channel,
            situation=situation,
            samples=len(items),
            advanced=sum(int(s.advanced) for s in items),
            insights=_insights(items),
            shapes=_shapes(items),
        )
    return out


_cache: dict[int, tuple[float, dict[tuple[str, str], ReplyLearning]]] = {}


def company_learning(db: Session, company_id: int) -> dict[tuple[str, str], ReplyLearning]:
    now = time.monotonic()
    hit = _cache.get(int(company_id))
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    data = learn(collect_samples(db, int(company_id)))
    _cache[int(company_id)] = (now, data)
    return data


def clear_cache() -> None:
    _cache.clear()


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _insight_line(i: ReplyInsight) -> str:
    best = VALUE_LABELS.get((i.trait, i.value), i.value)
    other = VALUE_LABELS.get((i.trait, i.other_value), i.other_value)
    return (
        f"- {best[:1].upper()}{best[1:]}: avanzó a reunión el {_pct(i.advance_rate)} de las veces "
        f"(contra {_pct(i.other_advance_rate)} de «{other}»)."
    )


def prompt_block_from_learning(
    data: dict[tuple[str, str], ReplyLearning], *, channel: str, inbound_text: str | None
) -> str:
    ch = (channel or "").strip().lower()
    situation = situation_of(inbound_text) if inbound_text else None
    candidates = []
    if ch and situation:
        candidates.append(data.get((ch, situation)))
    if ch:
        candidates.append(data.get((ch, ALL)))
    chosen = next((c for c in candidates if c is not None and (c.insights or c.shapes)), None)
    if chosen is None:
        return ""
    label = {"email": "Email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp"}.get(chosen.channel, chosen.channel)
    lines = [
        f"[Qué funcionó al responder · {label} · cuando el prospecto {SITUATION_LABELS.get(chosen.situation, chosen.situation)}]",
        f"Aprendido de {chosen.samples} respuestas reales de esta empresa.",
    ]
    lines.extend(_insight_line(i) for i in chosen.insights[:3])
    if chosen.shapes:
        lines.append("Formas de respuesta que hicieron avanzar la conversación (forma, no texto):")
        lines.extend(f"- {s}" for s in chosen.shapes)
    lines.append(
        "Usalo como criterio de forma, no como plantilla: respondé a lo que dijo ESTE prospecto con sus datos, "
        "redactá desde cero y no reutilices frases de otras conversaciones. "
        "Si una pauta choca con las reglas de este turno (p. ej. no proponer reunión si pidió tiempo), mandan las reglas."
    )
    return "\n".join(lines)


def prompt_block(db: Session, company_id: int, channel: str | None, inbound_text: str | None = None) -> str:
    try:
        return prompt_block_from_learning(
            company_learning(db, company_id), channel=channel or "", inbound_text=inbound_text
        )
    except Exception:
        return ""


def summary(db: Session, company_id: int) -> list[dict[str, Any]]:
    """Para Analytics → «Qué está funcionando» · respuestas a inbound."""
    out: list[dict[str, Any]] = []
    order = {"email": 0, "linkedin": 1, "whatsapp": 2}
    for (channel, situation), cl in sorted(
        company_learning(db, company_id).items(),
        key=lambda kv: (order.get(kv[0][0], 9), 0 if kv[0][1] == ALL else 1, kv[0][1]),
    ):
        if not cl.insights and not (situation == ALL and cl.samples):
            continue
        out.append(
            {
                "channel": channel,
                "channel_label": {"email": "Email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp"}.get(channel, channel),
                "situation": situation,
                "situation_label": SITUATION_LABELS.get(situation, situation),
                "samples": cl.samples,
                "advance_rate": _rate(cl.advanced, cl.samples),
                "insights": [
                    {
                        "trait": i.trait,
                        "trait_label": TRAIT_LABELS.get(i.trait, i.trait),
                        "value_label": VALUE_LABELS.get((i.trait, i.value), i.value),
                        "other_label": VALUE_LABELS.get((i.trait, i.other_value), i.other_value),
                        "n": i.n,
                        "other_n": i.other_n,
                        "advance_rate": i.advance_rate,
                        "other_advance_rate": i.other_advance_rate,
                        "reply_rate": i.reply_rate,
                        "other_reply_rate": i.other_reply_rate,
                        "lift": i.lift,
                    }
                    for i in cl.insights
                ],
                "shapes": cl.shapes,
            }
        )
    return out
