"""Pedido de baja (unsubscribe): no se responde, se corta la secuencia y no se vuelve a prospectar."""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect

logger = logging.getLogger(__name__)

OPT_OUT_OBJECTION = "opt_out"
OPT_OUT_EXCLUSION_PROVIDER = "opt_out"

_OPT_OUT_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\bunsubscribe\b",
        r"\bopt[\s-]?out\b",
        r"\bremove me\b",
        r"\bstop (emailing|contacting|messaging|writing)\b",
        r"\b(do not|don'?t) (contact|email|message|write)( to)? (me|us)\b",
        r"\b(dar|darme|darnos|dame|danos|denme|dennos|deme|me doy|nos damos)\s+de\s+baja\b",
        r"\b(quiero|queremos|pido|pedimos|solicito)\s+(la|mi|nuestra)?\s*baja\b",
        r"\bno\s+(me|nos)\s+(escrib|contact|mand|envi|llam)\w*\s+(mas|nunca)\b",
        r"\bno\s+(me|nos)\s+(contacten|contactes|contactis|escriban|escribas|escribis|manden|mandes|envien)\b",
        r"\b(deja|dejen|deje|dejes|dejar|dejame|dejenme|dejennos|dejanos)\s+de\s+(escrib|contact|mand|envi|molest)\w*",
        r"\bno\s+(quiero|queremos)\s+(recibir\s+mas|que\s+(me|nos)\s+(escrib|contact)\w*)",
        r"\b(saca|saque|borra|borre|elimin)\w*\s+de\s+(la|su|tu|sus|tus|esta|esa|vuestra)\s+(lista|base|mailing)",
        r"\bno\s+quiero\s+que\s+(me|nos)\s+(escriban|escribas|contacten|contactes|hablen|hables)",
        r"\bno\s+quiero\s+hablar\s+(mas|nunca)",
        r"\bno\s+me\s+escribas(\s+(mas|nunca))?\b",
        r"\bnunca\s+mas\s+(me|nos)\s+(escriban|escribas|contacten|hablen)",
        r"\bbloqueame\b",
        r"\bno(\s+me)?\s+molestes\b",
        r"\bdejame\s+en\s+paz\b",
        r"\bno\s+me\s+(hablen|hables)\s+(mas|nunca)",
    )
)

_BARE_OPT_OUT_RE = re.compile(r"^\s*(baja|stop|remove|unsubscribe)\s*[.!]*\s*$")


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in norm if not unicodedata.combining(ch)).lower()


def strip_quoted_reply(text: str | None) -> str:
    """Devuelve solo lo que escribió la persona (sin el mail citado debajo)."""
    from app.services.meeting_slot_parser import strip_email_reply_quotes

    out = strip_email_reply_quotes(text)
    return out or (text or "").strip()


def is_opt_out_request(text: str | None) -> bool:
    if not isinstance(text, str):
        return False
    own = _fold(strip_quoted_reply(text))
    if not own.strip():
        return False
    if _BARE_OPT_OUT_RE.match(own):
        return True
    return any(rx.search(own) for rx in _OPT_OUT_PATTERNS)


def is_opted_out(prospect: Prospect) -> bool:
    return (getattr(prospect, "objection_type", None) or "").strip() == OPT_OUT_OBJECTION


def is_suppressed(prospect: Prospect) -> bool:
    """Pidió la baja y el SDR no lo reactivó a mano."""
    from app.services.multichannel_sequence import SEQUENCE_GROUP_ENCAJONADO

    return is_opted_out(prospect) and getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_ENCAJONADO


def should_suppress_inbound(prospect: Prospect, inbound_text: str | None) -> bool:
    return is_suppressed(prospect) or is_opt_out_request(inbound_text)


def _exclude_email(db: Session, company_id: int, email: str | None) -> None:
    from app.services.crm import exclusions as crm_exclusions

    em = crm_exclusions.normalize_email(email)
    if not em:
        return
    crm_exclusions.merge_hits(
        db,
        company_id,
        OPT_OUT_EXCLUSION_PROVIDER,
        [
            crm_exclusions.ExclusionHit(
                crm_exclusions.MATCH_EMAIL,
                em,
                label=em,
                reason="Pidió la baja",
            )
        ],
    )


def apply_opt_out(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    channel: str,
    inbound_text: str = "",
) -> None:
    """Corta todo contacto con el prospecto. Idempotente."""
    from app.services import multichannel_sequence as mseq
    from app.services import pipeline_sync
    from app.services.ai_decision_log import record_ai_decision

    already = is_opted_out(prospect)
    now = datetime.now(UTC)
    prospect.objection_type = OPT_OUT_OBJECTION
    if not already:
        prospect.objection_detected_at = now
    prospect.status = ProspectStatus.not_interested.value
    prospect.interest_level = "low"
    mseq.mark_encajonado(prospect)
    prospect.ai_paused = True
    prospect.next_touch_at = None
    prospect.defer_resume_at = None
    prospect.sequence_playbook_draft = None
    prospect.whatsapp_assisted_draft = None
    prospect.whatsapp_assist_session_id = None
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_session_id = None
    prospect.next_best_action = "No contactar: pidió la baja"

    db.execute(
        update(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect.id,
            OutreachTask.status == "pending",
        )
        .values(status="cancelled", updated_at=now)
    )
    pipeline_sync.sync_pipeline_from_status(prospect)

    try:
        _exclude_email(db, int(campaign.company_id), prospect.email)
    except Exception:
        logger.exception("opt-out exclusion failed prospect_id=%s", prospect.id)

    if already:
        return

    ch = (channel or "email").strip().lower() or "email"
    name = prospect.name or prospect.email or f"#{prospect.id}"
    mseq._append_log(
        campaign,
        f"Lista negra por {ch} · {name}: no se responde y se cortó la secuencia",
        kind="info",
        channel=ch,
    )
    record_ai_decision(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        event_type="inbound_opt_out",
        decision="opt_out",
        summary="Pidió la baja: sin respuesta, secuencia cortada y contacto excluido",
        payload={"channel": ch, "inbound_snippet": (inbound_text or "")[:300]},
    )
    logger.info("prospect opt-out prospect_id=%s channel=%s", prospect.id, ch)
