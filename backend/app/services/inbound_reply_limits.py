"""Tope de respuestas IA por secuencia / conversación (costo + anti-spam)."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect


def max_ai_replies_per_sequence() -> int:
    raw = (os.getenv("NEXUS_MAX_AI_REPLIES_PER_SEQUENCE") or "12").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 12
    return max(1, min(n, 100))


def count_ai_outbound_replies(db: Session, *, prospect_id: int, since=None) -> int:
    stmt = select(func.count(OutreachMessage.id)).where(
        OutreachMessage.prospect_id == int(prospect_id),
        OutreachMessage.direction == "outbound",
        OutreachMessage.sender_type == "ai",
    )
    if since is not None:
        stmt = stmt.where(OutreachMessage.created_at >= since)
    return int(db.scalar(stmt) or 0)


def prospect_commercial_testing(prospect: Prospect) -> bool:
    """QA / simulación: mock Calendar, sin tope IA, borrador en reconnect."""
    if bool(getattr(prospect, "commercial_state_is_testing", False)):
        return True
    name = (getattr(prospect, "name", None) or "").strip().lower()
    company = (getattr(prospect, "company_name", None) or "").strip().lower()
    return name.startswith("wa qa ") or company.startswith("wa qa ")


def _is_local_test_sequence_prospect(prospect: Prospect) -> bool:
    """Test 1 / Test 3 / … en celular real: exime tope, pero SÍ auto-envía."""
    name = (getattr(prospect, "name", None) or "").strip().lower()
    company = (getattr(prospect, "company_name", None) or "").strip().lower()
    return bool(
        re.match(r"^test(\s+\d+)?\b", name) or re.match(r"^test(\s+\d+)?\b", company)
    )


_QA_INBOUND_ID_RE = re.compile(r"(?:^|:)(qa-|val-)", re.I)


def leftover_qa_whatsapp_prospect(prospect: Prospect) -> bool:
    """Ficha de batería QA: no auto-enviar a un celular real."""
    if prospect_commercial_testing(prospect):
        return True
    email = (getattr(prospect, "email", None) or "").strip().lower()
    return email.startswith("wa.qa.")


def leftover_qa_inbound_id(inbound_id: str | None) -> bool:
    return bool(_QA_INBOUND_ID_RE.search((inbound_id or "").strip().lower()))


def whatsapp_recover_max_age_seconds() -> int:
    raw = (os.getenv("NEXUS_WA_RECOVER_MAX_AGE_SEC") or "720").strip()
    try:
        sec = int(raw)
    except ValueError:
        sec = 720
    return max(180, min(sec, 1800))


def sequence_ai_reply_cap_exempt(prospect: Prospect) -> bool:
    """Prospectos de QA/simulación o Test N local: no consumen el tope de producción."""
    return prospect_commercial_testing(prospect) or _is_local_test_sequence_prospect(
        prospect
    )


def sequence_ai_reply_cap_reached(db: Session, prospect: Prospect) -> tuple[bool, int, int]:
    """True si ya se alcanzó el tope de réplicas IA en esta secuencia."""
    cap = max_ai_replies_per_sequence()
    started = getattr(prospect, "sequence_started_at", None)
    n = count_ai_outbound_replies(db, prospect_id=int(prospect.id), since=started)
    if sequence_ai_reply_cap_exempt(prospect):
        return False, n, cap
    return n >= cap, n, cap


def cap_skip_detail(prospect: Prospect, count: int, cap: int) -> str:
    return f"tope de respuestas IA ({count}/{cap}) en esta secuencia — prospecto {prospect.id}"


def whatsapp_auto_reply_burst_window_seconds() -> int:
    raw = (os.getenv("NEXUS_WA_AUTO_REPLY_BURST_SEC") or "180").strip()
    try:
        sec = int(raw)
    except ValueError:
        sec = 180
    return max(30, min(sec, 3600))


def whatsapp_auto_reply_burst_max_per_window() -> int:
    raw = (os.getenv("NEXUS_WA_AUTO_REPLY_BURST_MAX") or "1").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 1
    return max(1, min(n, 10))


def whatsapp_auto_reply_burst_blocks(db: Session, *, prospect_id: int) -> tuple[bool, str]:
    """Solo 1:1. Si el prospecto escribió, se responde. Sin espera de minutos.

    Lo único que se frena: segundo envío IA sin un inbound nuevo en el medio (la ráfaga).
    """
    last_ai = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.sender_type == "ai",
        )
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
    ).first()
    if last_ai is None or last_ai.created_at is None:
        return False, ""
    inbound_after = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.prospect_id == int(prospect_id),
                OutreachMessage.direction == "inbound",
                OutreachMessage.channel == "whatsapp",
                OutreachMessage.created_at > last_ai.created_at,
            )
        )
        or 0
    )
    if inbound_after > 0:
        return False, ""
    return True, "sin inbound nuevo tras el último envío IA"
