"""Historial de conversación con el prospecto, igual para mail, WhatsApp y LinkedIn.

Solo entra lo que el prospecto realmente vio o escribió: sin borradores no enviados,
sin notas internas de Nexus y sin el mail citado debajo de cada respuesta.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outreach import OutreachMessage

CHANNEL_LABELS = {
    "email": "Mail",
    "whatsapp": "WhatsApp",
    "linkedin": "LinkedIn",
    "call": "Llamada",
}

_LEADING_TAG_RE = re.compile(r"^\s*\[([^\]\n]{1,120})\]\s*")
_SUBJECT_LINE_RE = re.compile(r"^\s*asunto\s*:[^\n]*\n?", re.IGNORECASE)
_DRAFT_TAG_RE = re.compile(r"borrador", re.IGNORECASE)
_SYSTEM_SENT_TAG_RE = re.compile(r"enviad|env[ií]o|respuesta autom[aá]tica nexus", re.IGNORECASE)
_INTERNAL_TAG_RE = re.compile(r"^nexus\s*[—-]", re.IGNORECASE)


def _split_tags(text: str) -> tuple[list[str], str]:
    tags: list[str] = []
    body = (text or "").replace("\r\n", "\n").strip()
    while True:
        m = _LEADING_TAG_RE.match(body)
        if m:
            tags.append(m.group(1).strip())
            body = body[m.end():]
            continue
        s = _SUBJECT_LINE_RE.match(body)
        if s and tags:
            body = body[s.end():].lstrip("\n")
            continue
        break
    return tags, body.strip()


def history_text(message: Any) -> str | None:
    """Texto tal como lo vio el prospecto, o None si no es parte de la conversación real."""
    raw = getattr(message, "message", None) or ""
    if not isinstance(raw, str) or not raw.strip():
        return None
    tags, body = _split_tags(raw)
    direction = (getattr(message, "direction", None) or "").strip().lower()
    channel = (getattr(message, "channel", None) or "").strip().lower()
    sender = (getattr(message, "sender_type", None) or "").strip().lower()

    if direction == "auto_reply":
        return None
    if direction == "inbound":
        if channel == "email":
            from app.services.meeting_slot_parser import strip_email_reply_quotes

            body = strip_email_reply_quotes(body) or body
        return body or None

    if any(_DRAFT_TAG_RE.search(t) for t in tags):
        return None
    if any(_INTERNAL_TAG_RE.search(t) for t in tags):
        return None
    if sender == "system" and not any(_SYSTEM_SENT_TAG_RE.search(t) for t in tags):
        return None
    return body or None


def conversation_payload(messages: Sequence[Any]) -> list[dict[str, str]]:
    """Formato que consumen los prompts: sender_type / direction / channel / message."""
    rows: list[dict[str, str]] = []
    for m in messages:
        text = history_text(m)
        if not text:
            continue
        direction = (getattr(m, "direction", None) or "").strip().lower()
        sender = (getattr(m, "sender_type", None) or "").strip().lower()
        if direction == "outbound" and sender == "system":
            sender = "ai"
        rows.append(
            {
                "sender_type": sender or ("prospect" if direction == "inbound" else "ai"),
                "direction": direction,
                "channel": (getattr(m, "channel", None) or "").strip().lower(),
                "message": text,
            }
        )
    return rows


def speaker_line(item: dict[str, str], *, max_chars: int = 360) -> str | None:
    msg = (item.get("message") or "").strip().replace("\n", " ")
    if not msg:
        return None
    who = "Prospecto" if (item.get("direction") or "") == "inbound" else "Nosotros"
    label = CHANNEL_LABELS.get((item.get("channel") or "").strip().lower())
    head = f"{who} por {label}" if label else who
    return f"- {head}: {msg[:max_chars]}"


def digest_from_payload(payload: Sequence[dict[str, str]], *, limit: int = 18) -> str:
    lines = [ln for ln in (speaker_line(item) for item in payload[-limit:]) if ln]
    return "\n".join(lines) if lines else "(vacío)"


def load_conversation(
    db: Session, prospect_id: int, *, channel: str | None = None
) -> list[OutreachMessage]:
    """Solo mensajes de *este* prospecto. channel=whatsapp aísla el hilo WA (como Gmail)."""
    stmt = select(OutreachMessage).where(OutreachMessage.prospect_id == int(prospect_id))
    if channel:
        stmt = stmt.where(OutreachMessage.channel == channel.strip().lower())
    return list(
        db.scalars(stmt.order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())).all()
    )


def conversation_digest(db: Session, prospect_id: int, *, limit: int = 18) -> str:
    return digest_from_payload(conversation_payload(load_conversation(db, prospect_id)), limit=limit)
