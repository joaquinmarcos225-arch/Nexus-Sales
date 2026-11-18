"""Resumen conversacional corto por prospecto — para SDR (UI) e IA (compose)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect

_INTEREST_LABELS = {
    "low": "bajo",
    "medium": "medio",
    "high": "alto",
}

_OBJECTION_LABELS = {
    "timing": "mal timing / más adelante",
    "no_time": "sin tiempo ahora",
    "competitor": "usa competencia",
    "not_priority": "no es prioridad",
    "send_info": "pidió información",
    "not_interested": "no interesado",
    "other": "objeción genérica",
}

_STATE_LABELS = {
    "sin_conversacion": "sin conversación",
    "abriendo": "abriendo diálogo",
    "calificando": "calificando",
    "negociando": "negociando",
    "agendando": "agendando reunión",
    "cerrado_ganado": "cerrado ganado",
    "cerrado_perdido": "cerrado perdido",
    "pausado": "pausado",
}

_PROMISE_RE = re.compile(
    r"(?i)\b("
    r"te\s+aviso|te\s+confirmo|te\s+escribo|te\s+respondo|"
    r"la\s+semana\s+que\s+viene|la\s+pr[oó]xima\s+semana|"
    r"el\s+lunes|el\s+martes|el\s+mi[eé]rcoles|el\s+jueves|el\s+viernes|"
    r"ma[nñ]ana|pasado\s+ma[nñ]ana|"
    r"agend(?:amos|emos|emos)|coordin(?:amos|emos)|"
    r"i'?ll\s+(get\s+back|confirm|reply|email)|next\s+week|"
    r"let'?s\s+(schedule|book|set\s+up)"
    r")\b"
)


def _now() -> datetime:
    return datetime.now(UTC)


def _clip(text: str | None, n: int = 160) -> str:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return ""
    if len(raw) <= n:
        return raw
    return raw[: max(0, n - 1)].rstrip() + "…"


def infer_prospect_promise(inbound_text: str | None) -> str | None:
    text = (inbound_text or "").strip()
    if not text:
        return None
    m = _PROMISE_RE.search(text)
    if not m:
        return None
    # Prefer a short clause around the match.
    start = max(0, m.start() - 20)
    end = min(len(text), m.end() + 60)
    snippet = _clip(text[start:end], 120)
    return snippet or None


def _last_inbound_message(db: Session | None, prospect: Prospect) -> OutreachMessage | None:
    if db is None:
        return None
    return db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
        )
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(1)
    ).first()


def _outbound_inbound_counts(db: Session | None, prospect: Prospect) -> tuple[int, int]:
    if db is None:
        out_n = int(getattr(prospect, "outreach_touch_count", 0) or 0)
        return out_n, 1 if getattr(prospect, "last_inbound_at", None) else 0
    from sqlalchemy import func

    out_n = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.direction == "outbound",
            )
        )
        or 0
    )
    in_n = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.direction == "inbound",
                OutreachMessage.sender_type == "prospect",
            )
        )
        or 0
    )
    return out_n, in_n


def assemble_conversation_summary_parts(
    *,
    prospect_name: str | None = None,
    interest_level: str | None = None,
    objection_type: str | None = None,
    conversation_state: str | None = None,
    next_best_action: str | None = None,
    last_inbound_text: str | None = None,
    last_inbound_channel: str | None = None,
    outbound_count: int = 0,
    inbound_count: int = 0,
    status: str | None = None,
) -> dict[str, Any]:
    interest = (interest_level or "low").strip().lower() or "low"
    objection = (objection_type or "").strip().lower() or None
    if objection in ("none", "null", ""):
        objection = None
    state = (conversation_state or "sin_conversacion").strip().lower()
    channel = (last_inbound_channel or "").strip().lower() or None
    inbound_snip = _clip(last_inbound_text, 140)
    promise = infer_prospect_promise(last_inbound_text)

    what_bits: list[str] = []
    if outbound_count or inbound_count:
        what_bits.append(f"{outbound_count} envío(s), {inbound_count} respuesta(s)")
    if channel and inbound_snip:
        what_bits.append(f"último inbound por {channel}: «{inbound_snip}»")
    elif inbound_snip:
        what_bits.append(f"último inbound: «{inbound_snip}»")
    elif state and state != "sin_conversacion":
        what_bits.append(f"estado: {_STATE_LABELS.get(state, state)}")
    if (status or "").strip() == "meeting_booked":
        what_bits.append("reunión agendada")
    what_happened = "; ".join(what_bits) if what_bits else "aún sin intercambio relevante"

    next_step = _clip(next_best_action, 120) or None
    if not next_step:
        if promise:
            next_step = "Seguir la promesa del prospecto y confirmar el próximo paso"
        elif objection == "not_interested":
            next_step = "Cerrar con respeto o handoff"
        elif interest == "high":
            next_step = "Proponer reunión concreta"
        elif inbound_count:
            next_step = "Responder el último inbound manteniendo el hilo"
        else:
            next_step = "Continuar secuencia o primer contacto"

    return {
        "what_happened": what_happened,
        "objection": objection,
        "objection_label": _OBJECTION_LABELS.get(objection, objection) if objection else None,
        "interest_level": interest,
        "interest_label": _INTEREST_LABELS.get(interest, interest),
        "conversation_state": state,
        "conversation_state_label": _STATE_LABELS.get(state, state),
        "next_step": next_step,
        "promise": promise,
        "last_inbound_channel": channel,
        "prospect_name": (prospect_name or "").strip() or None,
    }


def format_conversation_summary(parts: dict[str, Any]) -> str:
    """Texto corto legible (2–4 frases) para UI e IA."""
    chunks: list[str] = []
    what = (parts.get("what_happened") or "").strip()
    if what:
        chunks.append(what[0].upper() + what[1:] if len(what) > 1 else what)

    interest_label = parts.get("interest_label") or parts.get("interest_level")
    obj_label = parts.get("objection_label")
    mid = f"Interés {interest_label}."
    if obj_label:
        mid = f"Interés {interest_label}; objeción: {obj_label}."
    chunks.append(mid)

    promise = (parts.get("promise") or "").strip()
    if promise:
        chunks.append(f"Promesa del prospecto: {promise}.")

    next_step = (parts.get("next_step") or "").strip()
    if next_step:
        chunks.append(f"Próximo paso: {next_step}.")

    text = " ".join(chunks).strip()
    return _clip(text, 480)


def conversation_summary_prompt_block(summary: str | None) -> str:
    text = (summary or "").strip()
    if not text:
        return ""
    return (
        "[Resumen del hilo — priorizá coherencia con esto; no lo cites literal]\n"
        f"{text}"
    )


def refresh_conversation_summary(
    db: Session | None,
    prospect: Prospect,
    *,
    last_inbound_text: str | None = None,
    last_inbound_channel: str | None = None,
) -> str:
    """Recalcula y persiste el resumen en el prospecto. Devuelve el texto."""
    inbound_text = last_inbound_text
    inbound_channel = last_inbound_channel
    if inbound_text is None and db is not None:
        msg = _last_inbound_message(db, prospect)
        if msg is not None:
            inbound_text = msg.message or ""
            inbound_channel = inbound_channel or getattr(msg, "channel", None)

    out_n, in_n = _outbound_inbound_counts(db, prospect)
    parts = assemble_conversation_summary_parts(
        prospect_name=getattr(prospect, "name", None),
        interest_level=getattr(prospect, "interest_level", None),
        objection_type=getattr(prospect, "objection_type", None),
        conversation_state=getattr(prospect, "conversation_state", None),
        next_best_action=getattr(prospect, "next_best_action", None),
        last_inbound_text=inbound_text,
        last_inbound_channel=inbound_channel,
        outbound_count=out_n,
        inbound_count=in_n,
        status=getattr(prospect, "status", None),
    )
    summary = format_conversation_summary(parts)
    prospect.conversation_summary = summary
    prospect.conversation_summary_updated_at = _now()
    return summary


def ensure_conversation_summary(db: Session | None, prospect: Prospect) -> str:
    existing = (getattr(prospect, "conversation_summary", None) or "").strip()
    if existing:
        return existing
    return refresh_conversation_summary(db, prospect)


def attach_summary_to_prospect_dict(prospect: Prospect, data: dict[str, str]) -> dict[str, str]:
    summary = (getattr(prospect, "conversation_summary", None) or "").strip()
    if summary:
        data["conversation_summary"] = summary
    return data
