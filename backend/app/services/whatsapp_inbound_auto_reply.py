"""Auto-respuesta WhatsApp: mismo delay/modo que Gmail, envío por Cloud API."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import InboundReplyMode, ProspectStatus
from app.models.prospect import Prospect
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services import conversation_intelligence as ci
from app.services import daily_send_limits as dsl
from app.services import followup_engine
from app.services import inbound_auto_reply as iar
from app.services import multichannel_sequence as mseq
from app.services.outreach_simulation import make_message
from app.models.whatsapp_meow import WhatsappMeowSession
from app.services.whatsapp_cloud_service import (
    is_whatsapp_cloud_api_enabled,
    normalize_whatsapp_digits,
    send_text_message,
)
from app.services.whatsapp_commercial import WhatsAppCommercialError, assert_can_send_whatsapp_meow
from app.services.whatsapp_meow_service import send_text_message as send_meow_text_message

logger = logging.getLogger(__name__)

AUTO_REPLY_MARKER_PREFIX = "[auto-reply:whatsapp:"


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _meow_inbound_auto_send_enabled() -> bool:
    """Si está off (default), las réplicas inbound van a cola WA asistida, no auto-envío Meow."""
    return _truthy_env("NEXUS_MEOW_INBOUND_AUTO_SEND")


def auto_reply_marker(inbound_id: str) -> str:
    return f"{AUTO_REPLY_MARKER_PREFIX}{inbound_id}]"


def _outbound_auto_reply_exists(db: Session, *, prospect_id: int, inbound_id: str) -> bool:
    """True si ya hay un outbound Nexus para este inbound (anti doble envío)."""
    from sqlalchemy import select

    from app.models.outreach import OutreachMessage

    marker = auto_reply_marker(iar._norm_gmail_mid(inbound_id))
    rows = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "whatsapp",
        )
        .order_by(OutreachMessage.id.desc())
        .limit(40)
    ).all()
    for row in rows:
        text = (row.message or "").strip()
        if text.startswith(marker):
            return True
    return False


def _strip_wa_inbound_prefix(text: str) -> str:
    body = (text or "").strip()
    for prefix in ("[WhatsApp · respuesta real]", "[WhatsApp"):
        if body.startswith(prefix):
            body = body.split("\n", 1)[-1].strip()
            break
    return body


def load_whatsapp_inbound_plain(db: Session, *, prospect_id: int, inbound_id: str) -> str:
    """El worker no trae el texto; lo lee del inbound persistido (nunca skip vacío)."""
    mid = iar._norm_gmail_mid(inbound_id)
    if not mid:
        return ""
    from sqlalchemy import select

    from app.models.outreach import OutreachMessage

    row = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.direction == "inbound",
            OutreachMessage.whatsapp_message_id == mid,
        )
        .order_by(OutreachMessage.id.desc())
    ).first()
    if row is None:
        row = db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.prospect_id == int(prospect_id),
                OutreachMessage.direction == "inbound",
                OutreachMessage.gmail_message_id == mid,
            )
            .order_by(OutreachMessage.id.desc())
        ).first()
    if row is None:
        return ""
    return _strip_wa_inbound_prefix(row.message or "")


def load_whatsapp_inbound_window(db: Session, *, prospect_id: int) -> str:
    """Todos los inbound desde el último outbound: si mandó 2 mensajes, contestamos los dos."""
    from sqlalchemy import select

    from app.models.outreach import OutreachMessage
    from app.services.whatsapp_reply_compose import merge_quoted_inbound

    last_out = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "whatsapp",
        )
        .order_by(OutreachMessage.id.desc())
    ).first()
    stmt = select(OutreachMessage).where(
        OutreachMessage.prospect_id == int(prospect_id),
        OutreachMessage.direction == "inbound",
        OutreachMessage.channel == "whatsapp",
    )
    if last_out is not None and last_out.created_at is not None:
        stmt = stmt.where(OutreachMessage.created_at >= last_out.created_at)
    rows = db.scalars(stmt.order_by(OutreachMessage.id.asc())).all()
    parts: list[str] = []
    for row in rows:
        parts.append(merge_quoted_inbound(_strip_wa_inbound_prefix(row.message or "")))
    return "\n".join(p for p in parts if p).strip()


def prefer_latest_slot_inbound(window_or_body: str) -> str:
    """Si el bloque tiene varias líneas y la última trae horario, priorizar esa.

    Evita que «10am no podés?» + «sí, a las 10:30» se parseé como 10:00 otra vez.
    """
    raw = (window_or_body or "").strip()
    if not raw:
        return raw
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", raw) if ln.strip()]
    if len(lines) <= 1:
        return raw
    last = lines[-1]
    try:
        from app.services.meeting_slot_parser import (
            parse_meeting_slot,
            prospect_proposed_meeting_slot,
        )

        if prospect_proposed_meeting_slot(last) and parse_meeting_slot(last) is not None:
            return last
    except Exception:
        pass
    return raw


def last_substantial_whatsapp_inbound_plain(
    db: Session, *, prospect_id: int
) -> str:
    """Último inbound con contenido (no '.' / vacío): para citas 'ya te lo dije'."""
    from sqlalchemy import select

    from app.models.outreach import OutreachMessage
    from app.services.whatsapp_reply_compose import inbound_is_thin, merge_quoted_inbound

    rows = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.direction == "inbound",
            OutreachMessage.channel == "whatsapp",
        )
        .order_by(OutreachMessage.id.desc())
        .limit(8)
    ).all()
    for row in rows:
        text = merge_quoted_inbound(_strip_wa_inbound_prefix(row.message or ""))
        if text and not inbound_is_thin(text):
            return text
    return ""


def _whatsapp_inbound_still_needs_reply(db: Session, inbound: Any) -> bool:
    mid = (getattr(inbound, "whatsapp_message_id", None) or "").strip()
    if not mid:
        return False
    from app.models.outreach import OutreachMessage
    from sqlalchemy import func, select

    later = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.prospect_id == int(inbound.prospect_id),
                OutreachMessage.direction == "outbound",
                OutreachMessage.channel == "whatsapp",
                OutreachMessage.created_at > inbound.created_at,
            )
        )
        or 0
    )
    if later > 0:
        return False
    created = getattr(inbound, "created_at", None)
    if created is None:
        return False
    from datetime import UTC, datetime

    from app.services.inbound_reply_limits import whatsapp_recover_max_age_seconds

    raw = created
    if getattr(raw, "tzinfo", None) is not None:
        raw = raw.astimezone(UTC).replace(tzinfo=None)
    age = (datetime.now(UTC).replace(tzinfo=None) - raw).total_seconds()
    if age > whatsapp_recover_max_age_seconds():
        return False
    # Recover no puede saltarse la espera humana: el worker manda lo scheduled.
    if age < 30:
        return False
    rec = iar.get_auto_reply_receipt(db, int(inbound.prospect_id), mid)
    if rec is not None:
        outcome = (rec.outcome or "").strip()
        if outcome == "sent":
            return False
        if outcome not in {"failed", "skipped"}:
            return False
    prospect = db.get(Prospect, int(inbound.prospect_id))
    if prospect is None:
        return False
    from app.services.inbound_reply_limits import leftover_qa_inbound_id, leftover_qa_whatsapp_prospect
    from app.services.whatsapp_assisted_service import prospect_whatsapp_digits
    from app.services.whatsapp_humanize import prospect_is_composing

    if leftover_qa_whatsapp_prospect(prospect) or leftover_qa_inbound_id(mid):
        return False
    from app.services.meeting_scheduling_extras import auto_reply_silenced

    if auto_reply_silenced(prospect):
        return False
    if prospect_is_composing(prospect_whatsapp_digits(prospect) or ""):
        return False
    from app.services.inbound_reply_limits import sequence_ai_reply_cap_reached
    from app.services.prospect_opt_out import is_suppressed

    if is_suppressed(prospect):
        return False
    capped, _, _ = sequence_ai_reply_cap_reached(db, prospect)
    if capped:
        return False
    return True


def recover_unanswered_whatsapp_inbounds(db: Session, *, limit: int = 20) -> int:
    """Si un inbound WA quedó sin respuesta (skip/fail), el worker la manda. Sin push manual."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.outreach import OutreachMessage
    from app.services.whatsapp_meow_sequence import seller_connected_meow_session

    from app.services.inbound_reply_limits import whatsapp_recover_max_age_seconds

    cutoff = datetime.now(UTC) - timedelta(seconds=whatsapp_recover_max_age_seconds())
    rows = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.direction == "inbound",
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.created_at >= cutoff,
        )
        .order_by(OutreachMessage.id.desc())
        .limit(limit)
    ).all()
    sent = 0
    seen_prospects: set[int] = set()
    for row in rows:
        pid = int(row.prospect_id)
        if pid in seen_prospects:
            continue
        if not _whatsapp_inbound_still_needs_reply(db, row):
            continue
        prospect = db.get(Prospect, pid)
        campaign = db.get(Campaign, int(row.campaign_id or 0))
        if prospect is None or campaign is None:
            continue
        mid = (row.whatsapp_message_id or "").strip()
        plain = _strip_wa_inbound_prefix(row.message or "")
        meow = seller_connected_meow_session(
            db,
            company_id=int(campaign.company_id),
            seller_user_id=int(campaign.seller_id or 0),
        )
        try:
            outcome = deliver_whatsapp_auto_reply_for_inbound(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_id=mid,
                inbound_plain=plain,
                force_immediate=True,
                allow_meta_send=meow is None,
                allow_meow_send=meow is not None,
                meow_session_row=meow,
            )
        except Exception:
            logger.exception("recover unanswered wa inbound=%s prospect=%s", mid[:20], prospect.id)
            continue
        seen_prospects.add(pid)
        if outcome == "sent":
            sent += 1
            logger.info(
                "[wa_auto_reply] recovered unanswered inbound=%s prospect_id=%s",
                mid[:24],
                prospect.id,
            )
    return sent


def _inbound_reply_mode(campaign: Campaign) -> str:
    return (getattr(campaign, "inbound_reply_mode", None) or InboundReplyMode.draft_only.value).strip()


def park_draft_for_auto_send(prospect: Prospect, draft: str) -> None:
    """Guarda el texto para el worker sin dejarlo en la cola manual del SDR."""
    from app.services import whatsapp_assisted_service as was

    prospect.whatsapp_assisted_draft = (draft or "").strip() or None
    was._set_assist_status(prospect, was.STATUS_AUTO_PENDING)


_HELP_DESK_CTA_RE = re.compile(r"en qu[eé] te (puedo|podemos) ayudar", re.I)


def _usable_locked_reply(text: str, *, inbound_plain: str = "") -> str:
    """No reutilizar un borrador viejo de 'soporte' si el hilo es outbound."""
    body = (text or "").strip()
    if not body:
        return ""
    if _HELP_DESK_CTA_RE.search(body):
        return ""
    if inbound_plain:
        from app.services.whatsapp_reply_compose import draft_off_topic_for_inbound

        if draft_off_topic_for_inbound(inbound_plain, body):
            return ""
    from app.services.whatsapp_humanize import is_ai_identity_question

    if is_ai_identity_question(inbound_plain) and "te escribo yo" not in body.lower():
        return ""
    from app.services.meeting_scheduling_extras import extract_email

    if extract_email(inbound_plain) and re.search(
        r"necesito tu mail|pas[aá]s tu mail|mand[oa].*invitaci", body, re.I
    ):
        return ""
    from app.services.whatsapp_reply_compose import inbound_is_product_aside

    if inbound_is_product_aside(inbound_plain) and re.search(
        r"necesito tu mail|pas[aá]s tu mail|mand[oa].*invitaci", body, re.I
    ):
        return ""
    return body


def _locked_reply_body_for_worker(
    db: Session,
    *,
    prospect: Prospect,
    inbound_id: str,
    force_immediate: bool,
    inbound_plain: str = "",
) -> str:
    """Texto acordado al inbound: no regenerar al ejecutar el worker (evita ráfagas distintas)."""
    if not force_immediate:
        return ""
    mid = iar._norm_gmail_mid(inbound_id)
    planned = iar._planned_reply(db, prospect.id, mid)
    if planned and (planned.get("body") or "").strip():
        usable = _usable_locked_reply(str(planned.get("body") or ""), inbound_plain=inbound_plain)
        if usable:
            return usable
    from app.services import whatsapp_assisted_service as was

    parked = (getattr(prospect, "whatsapp_assisted_draft", None) or "").strip()
    if parked and was.read_assist_status(prospect) == was.STATUS_AUTO_PENDING:
        return _usable_locked_reply(parked, inbound_plain=inbound_plain)
    return ""


def _wa_auto_send_burst_blocked(
    db: Session, *, prospect: Prospect, inbound_id: str
) -> tuple[bool, str]:
    from app.services.inbound_reply_limits import whatsapp_auto_reply_burst_blocks

    blocked, detail = whatsapp_auto_reply_burst_blocks(db, prospect_id=int(prospect.id))
    if blocked:
        logger.warning(
            "[wa_auto_reply] burst block prospect_id=%s inbound=%s detail=%s",
            prospect.id,
            iar._norm_gmail_mid(inbound_id)[:20],
            detail,
        )
    return blocked, detail


def _compose_reply(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound_plain: str,
) -> str:
    from app.services.whatsapp_reply_compose import compose_whatsapp_inbound_reply

    return (compose_whatsapp_inbound_reply(
        db,
        prospect=prospect,
        campaign=campaign,
        inbound_text=inbound_plain,
    ) or "").strip()


def _scheduling_reply(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    body: str,
    sig: Any,
    suggested: str,
) -> str | None:
    """Misma agenda que el mail: reserva en Calendar, pide el mail para la invitación, mueve o cancela."""
    from app.services.inbound_reply_limits import prospect_commercial_testing
    from app.services.inbound_turn_orchestrator import resolve_inbound_scheduling_reply

    try:
        decision = resolve_inbound_scheduling_reply(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=body,
            reply_objective=None,
            sig=sig,
            suggested_reply=suggested,
            channel="whatsapp",
            testing=prospect_commercial_testing(prospect),
        )
    except Exception:
        logger.exception("[wa_auto_reply] scheduling failed prospect_id=%s", prospect.id)
        return None
    if decision.action == "skip_autoresponder":
        return "skip_autoresponder"
    if decision.action == "normal_reply":
        return None
    if decision.action == "booking_failed":
        rb = (decision.reply_body or "").strip()
        if not rb:
            from app.services.meeting_booking import build_calendar_link_reply, usable_calendar_booking_link

            link = usable_calendar_booking_link(getattr(campaign, "calendar_link", None))
            if link:
                rb = build_calendar_link_reply(
                    prospect_name=prospect.name,
                    calendar_link=link,
                ).strip()
            else:
                rb = (
                    "Anoté el horario. "
                    "Te mando la invitación de Calendar en cuanto esté conectada la agenda."
                )
        return rb or None
    return (decision.reply_body or "").strip() or None


def _execute_whatsapp_delivery(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_id: str,
    reply_body: str,
    delivery: Literal["draft", "send"],
) -> str:
    from app.services import whatsapp_assisted_service as was

    text = (reply_body or "").strip()
    cid = int(campaign.company_id)
    marker = auto_reply_marker(inbound_id)

    if delivery == "send" and text:
        burst, _detail = _wa_auto_send_burst_blocked(db, prospect=prospect, inbound_id=inbound_id)
        if burst:
            return _execute_whatsapp_delivery(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_id=inbound_id,
                reply_body=text,
                delivery="draft",
            )

    if delivery == "draft" or not text:
        if text:
            was.mark_draft_suggested(db, prospect, campaign, text, log_event=True)
        iar.record_auto_reply_receipt(
            db,
            company_id=cid,
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=inbound_id,
            outcome="draft",
        )
        mseq._append_log(
            campaign,
            f"Nexus dejó borrador automático de WhatsApp · {prospect.name or prospect.id}",
            kind="outbound",
        )
        return "draft"

    to_digits = normalize_whatsapp_digits(prospect.phone, prospect.whatsapp)
    if not to_digits:
        logger.warning(
            "[wa_auto_reply] send skipped: no usable phone prospect_id=%s",
            prospect.id,
        )
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=inbound_id,
            reply_body=text,
            delivery="draft",
        )

    if not is_whatsapp_cloud_api_enabled():
        logger.info(
            "[wa_auto_reply] Cloud API off — draft only prospect_id=%s",
            prospect.id,
        )
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=inbound_id,
            reply_body=text,
            delivery="draft",
        )

    seller_id = int(campaign.seller_id or 0)
    if seller_id and not dsl.can_send(db, seller_id, dsl.KIND_WHATSAPP, cold=False):
        logger.info(
            "[wa_auto_reply] daily cap — draft prospect_id=%s",
            prospect.id,
        )
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=inbound_id,
            reply_body=text,
            delivery="draft",
        )

    try:
        out = send_text_message(to_digits=to_digits, body=text)
        wamid = (out.get("whatsapp_message_id") or "").strip() or None
        if not wamid and not out.get("dry_run"):
            raise RuntimeError("WhatsApp send returned empty message id")
    except Exception as exc:
        logger.exception(
            "[wa_auto_reply] send fail prospect_id=%s: %s — fallback to draft",
            prospect.id,
            exc,
        )
        mseq._append_log(
            campaign,
            f"Envío automático WhatsApp falló; Nexus deja borrador · {prospect.name or prospect.id}",
            kind="info",
        )
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=inbound_id,
            reply_body=text,
            delivery="draft",
        )

    hist_text = f"{marker}\n[WhatsApp · respuesta automática Nexus]\n\n{text}"
    db.add(
        make_message(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="ai",
            message=hist_text,
            channel="whatsapp",
            direction="outbound",
            whatsapp_message_id=wamid,
        )
    )
    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=text,
        closes_inbound_review=True,
    )
    prospect.whatsapp_assisted_draft = None
    prospect.whatsapp_assist_session_id = None
    was._set_assist_status(prospect, was.STATUS_SENT)
    if prospect.status not in (
        ProspectStatus.meeting_booked.value,
        ProspectStatus.not_interested.value,
    ):
        prospect.status = ProspectStatus.replied.value
    mseq._append_log(
        campaign,
        f"Nexus envió respuesta automática por WhatsApp · {prospect.name or prospect.id}",
        kind="outbound",
    )
    iar.record_auto_reply_receipt(
        db,
        company_id=cid,
        campaign_id=int(campaign.id),
        prospect_id=int(prospect.id),
        inbound_gmail_message_id=inbound_id,
        outcome="sent",
    )
    _close_chat_after_booking_closing_sent(
        db, prospect=prospect, reply_body=text, campaign=campaign
    )
    return "sent"


def _meow_session_for_campaign(
    db: Session, campaign: Campaign, *, session_row: WhatsappMeowSession | None = None
) -> WhatsappMeowSession | None:
    if session_row is not None:
        return session_row
    from app.services.whatsapp_meow_sequence import seller_connected_meow_session

    seller_id = int(campaign.seller_id or 0)
    if seller_id <= 0:
        return None
    return seller_connected_meow_session(
        db, company_id=int(campaign.company_id), seller_user_id=seller_id
    )


def _meow_session_usable(campaign: Campaign, session_row: WhatsappMeowSession | None) -> bool:
    if session_row is None:
        return False
    if str(session_row.status or "").strip().lower() != "connected":
        return False
    return int(session_row.user_id) == int(campaign.seller_id or 0)


def _execute_meow_auto_reply_delivery(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    session_row: WhatsappMeowSession,
    inbound_id: str,
    reply_body: str,
) -> str:
    """Envía réplica IA por sesión Meow conectada (mismo número que la secuencia)."""
    from app.models.company import Company
    from app.services.meeting_scheduling_extras import auto_reply_silenced
    from app.services.whatsapp_assisted_service import prospect_whatsapp_digits
    from app.services.whatsapp_meow_sequence import _digits_to_jid

    mid = iar._norm_gmail_mid(inbound_id)
    silence = auto_reply_silenced(prospect)
    if silence:
        logger.info(
            "[wa_meow_auto_reply] aborted before send prospect_id=%s silence=%s",
            prospect.id,
            silence,
        )
        iar.record_auto_reply_receipt(
            db,
            company_id=int(campaign.company_id),
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=mid,
            outcome="skipped",
        )
        return "skipped"

    if not _meow_inbound_auto_send_enabled():
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=inbound_id,
            reply_body=reply_body,
            delivery="draft",
        )

    text = (reply_body or "").strip()
    cid = int(campaign.company_id)
    marker = auto_reply_marker(mid)

    if not text:
        return "skipped"

    # Re-check por si otra corrida ganó la carrera durante el compose.
    if _outbound_auto_reply_exists(db, prospect_id=int(prospect.id), inbound_id=mid):
        logger.info(
            "[wa_meow_auto_reply] skip duplicate outbound prospect_id=%s inbound=%s",
            prospect.id,
            mid,
        )
        iar.record_auto_reply_receipt(
            db,
            company_id=cid,
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=mid,
            outcome="sent",
        )
        return "skipped_already"

    burst, detail = _wa_auto_send_burst_blocked(db, prospect=prospect, inbound_id=inbound_id)
    if burst:
        # Sin inbound nuevo en DB: no quemar draft (worker lo completa y nunca manda).
        logger.warning(
            "[wa_meow_auto_reply] burst skip prospect_id=%s inbound=%s detail=%s",
            prospect.id,
            mid[:48],
            detail,
        )
        return "skipped_burst"

    to_digits = prospect_whatsapp_digits(prospect)
    if not to_digits:
        logger.warning(
            "[wa_meow_auto_reply] send skipped: no phone prospect_id=%s",
            prospect.id,
        )
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=mid,
            reply_body=text,
            delivery="draft",
        )

    company = db.get(Company, campaign.company_id)
    if company is None:
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=mid,
            reply_body=text,
            delivery="draft",
        )

    try:
        assert_can_send_whatsapp_meow(db, company, digits=to_digits)
    except WhatsAppCommercialError as exc:
        logger.info(
            "[wa_meow_auto_reply] commercial gate prospect_id=%s: %s",
            prospect.id,
            exc.detail,
        )
        return _execute_whatsapp_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_id=mid,
            reply_body=text,
            delivery="draft",
        )

    from app.services.whatsapp_humanize import (
        note_person_sent,
        pace_outbound_bubbles,
        prepare_outbound_text,
        prospect_is_composing,
        wait_before_other_person,
    )
    from app.services.whatsapp_meow_service import set_chat_presence

    text = prepare_outbound_text(text, prospect=prospect, cold=False)
    # Nombre comercial corto (nunca «Contractia | Plataforma…»).
    try:
        from app.services.outreach_display_names import scrub_piped_product_title_in_body

        prod = getattr(getattr(prospect, "campaign", None), "product", None)
        pname = (getattr(prod, "name", None) or "").strip()
        if pname:
            text = scrub_piped_product_title_in_body(text, product_name=pname)
    except Exception:
        pass
    # Réplicas: 2–3 burbujas con sentido (mismo split que secuencia), no un choclo.
    from app.services.whatsapp_meow_sequence import split_whatsapp_default_bubbles

    from app.services.whatsapp_meow_sequence import _WA_BUBBLE_MAX_INBOUND

    bubbles = (
        split_whatsapp_default_bubbles(text, max_bubbles=_WA_BUBBLE_MAX_INBOUND)
        if text.strip()
        else []
    )
    # Anti-spam: no reenviar la PREGUNTA «¿Me pasás tu mail…?» si ya salió.
    # No tocar «Cuando me pases el mail…» (confirmación suave de horario).
    if bubbles:
        ask_q_re = re.compile(
            r"(?i)^\s*¿?\s*(?:me\s+)?pas[aá]s?\s+tu\s+mail|"
            r"^\s*¿?\s*pasame\s+tu\s+mail|"
            r"pas[aá]s?\s+tu\s+mail\s+(?:as[ií]|para)"
        )
        # Dedupe dentro del mismo envío (misma pregunta 2 veces en el body).
        seen_ask = False
        deduped_bubbles: list[str] = []
        for b in bubbles:
            if ask_q_re.search((b or "").strip()):
                if seen_ask:
                    continue
                seen_ask = True
            deduped_bubbles.append(b)
        bubbles = deduped_bubbles
        last_plain = ""
        try:
            from app.services.whatsapp_reply_compose import _last_whatsapp_outbound_plain

            last_plain = (_last_whatsapp_outbound_plain(prospect, db) or "").strip().lower()
        except Exception:
            last_plain = ""
        if last_plain and ask_q_re.search(last_plain):
            filtered = [b for b in bubbles if not ask_q_re.search((b or "").strip())]
            if filtered:
                bubbles = filtered
            elif bubbles and all(ask_q_re.search((b or "").strip()) for b in bubbles):
                logger.info(
                    "[wa_meow_auto_reply] skip duplicate ask-email prospect_id=%s",
                    prospect.id,
                )
                iar.record_auto_reply_receipt(
                    db,
                    company_id=cid,
                    campaign_id=int(campaign.id),
                    prospect_id=int(prospect.id),
                    inbound_gmail_message_id=mid,
                    outcome="skipped",
                )
                return "skipped"
        # Tras sacar el ask-mail: no reenviar el mismo «Genial, el martes…» del mensaje anterior.
        if last_plain and bubbles:
            rem = re.sub(r"\s+", " ", "\n".join(bubbles)).strip().lower()
            last_n = re.sub(r"\s+", " ", last_plain).strip().lower()
            if rem and last_n and (rem == last_n or rem in last_n or last_n.startswith(rem[:48])):
                logger.info(
                    "[wa_meow_auto_reply] skip duplicate outbound fragment prospect_id=%s",
                    prospect.id,
                )
                iar.record_auto_reply_receipt(
                    db,
                    company_id=cid,
                    campaign_id=int(campaign.id),
                    prospect_id=int(prospect.id),
                    inbound_gmail_message_id=mid,
                    outcome="skipped",
                )
                return "skipped"
    base_key = f"inbound-reply-{prospect.id}-{mid[:48]}"
    meow_msg = None
    to_jid = _digits_to_jid(to_digits)

    def _send_part(idx: int, bubble: str):
        # Siempre -pN: 1 burbuja y N burbujas comparten el mismo esquema (anti dup).
        part_key = f"{base_key}-p{idx + 1}"
        sent = send_meow_text_message(
            db,
            row=session_row,
            to=to_jid,
            body=bubble,
            idempotency_key=part_key,
        )
        if sent.status != "sent":
            raise RuntimeError(sent.error or "WhatsApp nativo no confirmó el envío.")
        return sent

    inbound_snapshot = load_whatsapp_inbound_window(db, prospect_id=int(prospect.id))
    sent_bubbles: list[str] = []

    def _abort_if_they_kept_talking() -> bool:
        db.expire_all()
        if prospect_is_composing(to_digits):
            return True
        now = load_whatsapp_inbound_window(db, prospect_id=int(prospect.id))
        return bool(now and inbound_snapshot and now != inbound_snapshot)

    def _send_tracked(idx: int, bubble: str):
        sent = _send_part(idx, bubble)
        sent_bubbles.append(bubble)
        return sent

    if prospect_is_composing(to_digits):
        logger.info(
            "[wa_meow_auto_reply] hold composing prospect_id=%s",
            prospect.id,
        )
        return "deferred_composing"

    try:
        wait_before_other_person(int(campaign.seller_id or 0), int(prospect.id))
        meow_msg = pace_outbound_bubbles(
            bubbles,
            send_fn=_send_tracked,
            set_typing=lambda state: set_chat_presence(
                row=session_row, to=to_jid, state=state
            ),
            should_abort=_abort_if_they_kept_talking,
            cold=False,
        )
    except Exception as exc:
        logger.exception(
            "[wa_meow_auto_reply] send fail prospect_id=%s: %s",
            prospect.id,
            exc,
        )
        iar.record_auto_reply_receipt(
            db,
            company_id=cid,
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=mid,
            outcome="failed",
        )
        return "failed"

    if not sent_bubbles:
        logger.info(
            "[wa_meow_auto_reply] aborted before send prospect_id=%s composing_or_new_inbound=1",
            prospect.id,
        )
        return "deferred_composing"

    if meow_msg is None or meow_msg.status != "sent":
        logger.warning(
            "[wa_meow_auto_reply] not confirmed prospect_id=%s status=%s err=%s",
            prospect.id,
            getattr(meow_msg, "status", None),
            ((getattr(meow_msg, "error", None) or "")[:200]),
        )
        iar.record_auto_reply_receipt(
            db,
            company_id=cid,
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=mid,
            outcome="failed",
        )
        return "failed"

    text = "\n\n".join(sent_bubbles)
    # Meow ya entregó: el bookkeeping NO puede dejar receipt en «sending»
    # (si falla, igual marcamos sent — idempotency evita re-enviar burbujas).
    try:
        note_person_sent(int(campaign.seller_id or 0), int(prospect.id))
        hist_text = f"{marker}\n[WhatsApp · respuesta automática Nexus · nativo]\n\n{text}"
        db.add(
            make_message(
                prospect_id=prospect.id,
                campaign_id=campaign.id,
                sender_type="ai",
                message=hist_text,
                channel="whatsapp",
                direction="outbound",
                whatsapp_message_id=meow_msg.whatsapp_msg_id or base_key,
            )
        )
        try:
            followup_engine.record_ai_outbound(
                db,
                prospect,
                campaign_calendar_link=campaign.calendar_link or "",
                outbound_text=text,
                closes_inbound_review=True,
            )
        except TypeError:
            followup_engine.record_ai_outbound(
                db,
                prospect,
                campaign_calendar_link=campaign.calendar_link or "",
                outbound_text=text,
            )
        from app.services import whatsapp_assisted_service as was

        prospect.whatsapp_assisted_draft = None
        prospect.whatsapp_assist_session_id = None
        was._set_assist_status(prospect, was.STATUS_SENT)
        if prospect.status not in (
            ProspectStatus.meeting_booked.value,
            ProspectStatus.not_interested.value,
        ):
            prospect.status = ProspectStatus.replied.value
        mseq._append_log(
            campaign,
            f"Nexus envió respuesta automática por WhatsApp nativo · {prospect.name or prospect.id}",
            kind="outbound",
        )
        _close_chat_after_booking_closing_sent(
            db, prospect=prospect, reply_body=text, campaign=campaign
        )
    except Exception:
        logger.exception(
            "[wa_meow_auto_reply] post-send bookkeeping failed prospect_id=%s "
            "(mensaje ya salió por Meow — forzamos receipt sent)",
            prospect.id,
        )
    iar.record_auto_reply_receipt(
        db,
        company_id=cid,
        campaign_id=int(campaign.id),
        prospect_id=int(prospect.id),
        inbound_gmail_message_id=mid,
        outcome="sent",
    )
    return "sent"


def _close_chat_after_booking_closing_sent(
    db: Session,
    *,
    prospect: Prospect,
    reply_body: str,
    campaign: Campaign | None = None,
) -> None:
    """Tras mandar el aviso de invite: estado agendado + silencio total de este chat."""
    from app.services.meeting_scheduling_extras import finalize_meeting_after_confirmation_sent

    camp = campaign
    if camp is None and getattr(prospect, "campaign_id", None) is not None:
        camp = db.get(Campaign, int(prospect.campaign_id))
    if camp is None:
        return
    finalize_meeting_after_confirmation_sent(
        db,
        prospect=prospect,
        campaign=camp,
        reply_body=reply_body,
    )


def deliver_whatsapp_auto_reply_for_inbound(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_id: str,
    inbound_plain: str | None = None,
    force_immediate: bool = False,
    allow_meta_send: bool = False,
    allow_meow_send: bool = False,
    meow_session_row: WhatsappMeowSession | None = None,
) -> str:
    """
    Tras inbound WhatsApp persistido.

    Por defecto deja borrador en cola Nexus (WhatsApp Web / extensión: el SDR envía).
    Auto-envía por Cloud API si allow_meta_send=True, o por Meow si allow_meow_send=True.
    """
    mid = iar._norm_gmail_mid(inbound_id)

    def _skip(reason: str, detail: str = "") -> str:
        prev = iar.get_auto_reply_receipt(db, prospect.id, mid)
        first_seen = prev is None
        if reason.startswith("skipped") or reason in iar._TERMINAL_AUTO_REPLY_OUTCOMES:
            iar.record_auto_reply_receipt(
                db,
                company_id=int(campaign.company_id),
                campaign_id=int(campaign.id),
                prospect_id=int(prospect.id),
                inbound_gmail_message_id=mid,
                outcome=reason,
            )
        if first_seen and reason not in iar._SILENT_ACTIVITY_OUTCOMES:
            iar.log_auto_reply_outcome_to_activity(
                campaign, prospect, mid, reason, detail=detail or "whatsapp", channel="whatsapp"
            )
        return reason

    if not iar.inbound_auto_reply_enabled():
        return _skip("skipped_disabled")

    campaign_paused = bool(getattr(campaign, "automation_paused", False))

    company = db.get(Company, campaign.company_id)
    if company and getattr(company, "global_automation_stop", False):
        return _skip("skipped_disabled", "parada global")

    from app.services.meeting_scheduling_extras import auto_reply_silenced

    silence = auto_reply_silenced(prospect)
    if silence:
        labels = {
            "ai_paused": "IA pausada para prospecto",
            "manual_pause": "secuencia pausada por el SDR: no auto-contestar",
            "meeting_booked": "reunión ya agendada: no contestar",
            "meeting_done": "reunión cerrada: no contestar",
        }
        return _skip("skipped", labels.get(silence, silence))

    from app.services.inbound_reply_limits import leftover_qa_inbound_id, leftover_qa_whatsapp_prospect

    if leftover_qa_whatsapp_prospect(prospect) or leftover_qa_inbound_id(mid):
        return _skip("skipped_qa", "ficha o inbound de QA: no auto-enviar")

    if not campaign.seller_id:
        return _skip("skipped", "sin vendedor asignado")

    prior_receipt = iar.get_auto_reply_receipt(db, prospect.id, mid)
    draft_only_receipt = (
        prior_receipt is not None
        and (prior_receipt.outcome or "").strip() == "draft"
        and allow_meow_send
    )
    if not draft_only_receipt and iar.already_auto_replied(
        db,
        prospect.id,
        mid,
        executing_scheduled_task=force_immediate,
    ):
        task = iar.get_pending_send_task(db, prospect.id, mid)
        detail = "pendiente en cola" if task else "ya finalizado"
        return _skip("skipped_already", detail)

    if _outbound_auto_reply_exists(db, prospect_id=int(prospect.id), inbound_id=mid):
        iar.record_auto_reply_receipt(
            db,
            company_id=int(campaign.company_id),
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=mid,
            outcome="sent",
        )
        return _skip("skipped_already", "outbound ya existe para este inbound")

    # Marca in-flight YA (antes de brain/OpenAI) para que otra corrida no duplique.
    iar.record_auto_reply_receipt(
        db,
        company_id=int(campaign.company_id),
        campaign_id=int(campaign.id),
        prospect_id=int(prospect.id),
        inbound_gmail_message_id=mid,
        outcome="sending",
    )
    try:
        db.commit()
    except Exception:
        db.flush()

    allowed = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    if allowed and "whatsapp" not in allowed:
        return _skip("skipped", "canal whatsapp no permitido en campaña")

    body = _strip_wa_inbound_prefix(inbound_plain or "")
    if len(body) < 2:
        body = load_whatsapp_inbound_plain(db, prospect_id=int(prospect.id), inbound_id=mid)
    window = load_whatsapp_inbound_window(db, prospect_id=int(prospect.id))
    if window:
        body = prefer_latest_slot_inbound(window)
    from app.services.whatsapp_reply_compose import inbound_is_thin, merge_quoted_inbound

    if inbound_is_thin(body):
        prev = last_substantial_whatsapp_inbound_plain(db, prospect_id=int(prospect.id))
        body = merge_quoted_inbound(body, prev)
    # El hilo puede haber crecido durante la espera: no reusar un lock viejo.
    locked_early = ""
    if not window:
        locked_early = _locked_reply_body_for_worker(
            db,
            prospect=prospect,
            inbound_id=mid,
            force_immediate=force_immediate,
            inbound_plain=body,
        )
    if len(body) < 2 and not (locked_early or "").strip():
        return _skip("skipped", "cuerpo inbound vacío")
    if len(body) < 2:
        body = "ok"

    from app.services import prospect_opt_out

    if prospect_opt_out.should_suppress_inbound(prospect, body):
        prospect_opt_out.apply_opt_out(
            db, campaign=campaign, prospect=prospect, channel="whatsapp", inbound_text=body
        )
        return _skip("skipped", "lista negra / opt-out")

    # Tras un «no» ya contestado: no reabrir con «dale, lo vemos en una llamada».
    from app.services.whatsapp_reply_compose import (
        inbound_is_courtesy_after_no,
        inbound_is_thin,
        last_whatsapp_ai_was_rejection_close,
        _NOT_INTERESTED_RE,
    )

    if last_whatsapp_ai_was_rejection_close(db, prospect_id=int(prospect.id)) and not _NOT_INTERESTED_RE.search(
        body.lower()
    ):
        if inbound_is_courtesy_after_no(body) or inbound_is_thin(body) or len(body.strip()) < 24:
            return _skip("skipped", "ya dijo que no — no reabrir venta")

    from app.services.inbound_reply_limits import cap_skip_detail, sequence_ai_reply_cap_reached

    capped, ai_count, ai_cap = sequence_ai_reply_cap_reached(db, prospect)
    if capped:
        assisted = (getattr(prospect, "whatsapp_assisted_draft", None) or "").strip()
        if assisted:
            iar.record_auto_reply_receipt(
                db,
                company_id=int(campaign.company_id),
                campaign_id=int(campaign.id),
                prospect_id=int(prospect.id),
                inbound_gmail_message_id=mid,
                outcome="draft",
            )
            return "draft"
        # skipped_cap (terminal): no reintentar en loop eterno.
        return _skip(
            "skipped_cap",
            cap_skip_detail(prospect, ai_count, ai_cap),
        )

    reply_body = ""
    if not window:
        reply_body = _locked_reply_body_for_worker(
            db,
            prospect=prospect,
            inbound_id=mid,
            force_immediate=force_immediate,
            inbound_plain=body,
        )

    from app.services.commercial_conversation_state import inbound_requires_orchestrator

    prior_interest = (prospect.interest_level or "low").strip() or "low"
    if not (reply_body or "").strip():
        try:
            sig = ci.build_signals_from_keywords(
                ci.normalize_inbound_text_for_classification(body),
                prior_interest,
            )
        except Exception:
            sig = ci.InboundSignals() if hasattr(ci, "InboundSignals") else None
    else:
        sig = None

    if not (reply_body or "").strip():
        from app.services.inbound_reply_limits import prospect_commercial_testing

        brain = None
        try:
            from app.services.nexus_brain import run_inbound_brain_turn

            brain = run_inbound_brain_turn(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_text=body,
                channel="whatsapp",
                sig=sig,
                reply_objective=None,
                testing=prospect_commercial_testing(prospect),
            )
        except Exception:
            logger.exception(
                "[wa_auto_reply] brain turn failed prospect_id=%s — fallback offline",
                prospect.id,
            )
            brain = None
        if brain is not None:
            # Persistir pending_slot / commercial.slot_at antes del humanize/schedule
            # (el commit temprano de «sending» deja el prospect expirado).
            try:
                db.flush()
            except Exception:
                logger.debug("flush after brain turn failed prospect_id=%s", prospect.id, exc_info=True)
            try:
                from app.services.ai_decision_log import record_ai_decision

                record_ai_decision(
                    db,
                    company_id=int(campaign.company_id),
                    campaign_id=int(campaign.id),
                    prospect_id=int(prospect.id),
                    event_type="brain_turn",
                    decision=f"brain_{brain.action}",
                    summary=(
                        f"Brain WA {'agente' if brain.used_agent else 'especialistas'} · "
                        f"{brain.action}"
                    ),
                    payload={
                        "used_agent": brain.used_agent,
                        "action": brain.action,
                        "channel": "whatsapp",
                        "trace": brain.agent_trace[-12:],
                    },
                    confidence=0.8 if brain.used_agent else 0.7,
                )
            except Exception:
                logger.debug("brain_turn log skipped", exc_info=True)

            if brain.action == "skip_autoresponder":
                return _skip("skipped", brain.skip_reason or "respuesta automática")
            if brain.should_skip and brain.action == "skip":
                return _skip("skipped", brain.skip_reason or "policy")

            reply_body = (brain.reply_body or "").strip()
            if not reply_body and inbound_requires_orchestrator(prospect, body):
                from app.services.whatsapp_reply_compose import whatsapp_inbound_offline_draft

                reply_body = whatsapp_inbound_offline_draft(
                    prospect, campaign, inbound_text=body, db=db
                )
    if not reply_body:
        from app.services.whatsapp_reply_compose import (
            continue_outbound_thread,
            last_whatsapp_ai_was_rejection_close as _rej_close,
            whatsapp_inbound_offline_draft,
        )

        offline = whatsapp_inbound_offline_draft(
            prospect, campaign, inbound_text=body, db=db
        )
        if (offline or "").strip():
            reply_body = offline
        elif _rej_close(db, prospect_id=int(prospect.id)):
            return _skip("skipped", "ya dijo que no — sin reabrir")
        else:
            reply_body = continue_outbound_thread(prospect, campaign, db=db)

    from app.services.whatsapp_reply_compose import (
        draft_off_topic_for_inbound,
        strip_whatsapp_email_signature,
        whatsapp_inbound_offline_draft,
    )

    reply_body = strip_whatsapp_email_signature(reply_body or "")
    if draft_off_topic_for_inbound(body, reply_body):
        reply_body = whatsapp_inbound_offline_draft(
            prospect, campaign, inbound_text=body, db=db
        )
        reply_body = strip_whatsapp_email_signature(reply_body or "")
    from app.services.whatsapp_reply_compose import (
        _last_whatsapp_outbound_plain,
        ensure_info_question_handled,
    )

    # Gate universal: cualquier pregunta de producto/info (no solo onboarding/CRM).
    before = (reply_body or "").strip()
    reply_body = ensure_info_question_handled(
        campaign,
        body,
        reply_body,
        booked_when=None,
        avoid_repeat=_last_whatsapp_outbound_plain(prospect, db) or before or None,
    )
    if before and reply_body != before:
        logger.warning(
            "[wa_auto_reply] info-question gate rewrote reply prospect_id=%s",
            prospect.id,
        )
    # Slot concreto / eligió alternativa: nunca re-preguntar ni re-pegar el mismo menú.
    try:
        from app.services.meeting_slot_parser import prospect_proposed_meeting_slot
        from app.services.inbound_turn_orchestrator import resolve_inbound_scheduling_reply

        low_reply = (reply_body or "").lower()
        last_out = (_last_whatsapp_outbound_plain(prospect, db) or "").strip().lower()
        asks_slot_again = bool(
            re.search(
                r"qu[eé]\s+d[ií]a\s+y\s+horario|qu[eé]\s+d[ií]a\s+te\s+queda|"
                r"horario\s+te\s+queda\s+bien|coordinamos\s+una\s+(llamada|charla)|"
                r"te\s+sirve\s+alguno|no\s+tengo\s+a\s+las",
                low_reply,
            )
        )
        repeats_alternatives = bool(
            last_out
            and low_reply
            and ("te sirve alguno" in last_out or "no tengo a las" in last_out)
            and (
                low_reply[:80] in last_out
                or last_out[:80] in low_reply
                or ("te sirve alguno" in low_reply and "te sirve alguno" in last_out)
            )
        )
        if prospect_proposed_meeting_slot(body) and (asks_slot_again or repeats_alternatives):
            booking_text = prefer_latest_slot_inbound(body)
            dec = resolve_inbound_scheduling_reply(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_text=booking_text,
                reply_objective="agendar",
                sig=sig,
                suggested_reply=reply_body or "",
                testing=False,
            )
            fixed = (getattr(dec, "reply_body", None) or "").strip()
            if fixed and fixed.lower() != low_reply:
                logger.warning(
                    "[wa_auto_reply] slot gate replaced ask-again prospect_id=%s action=%s",
                    prospect.id,
                    getattr(dec, "action", None),
                )
                reply_body = fixed
    except Exception:
        logger.debug("slot ask-again gate failed", exc_info=True)
    if not (reply_body or "").strip():
        return _skip("skipped", "sin texto de réplica")

    # «No gracias» → cierre + dejar de mirar este chat (no reabrir venta).
    from app.services.whatsapp_reply_compose import (
        apply_whatsapp_rejection_closeout,
        looks_like_rejection_close,
        _NOT_INTERESTED_RE as _ni_re,
    )

    if _ni_re.search(body.lower()) and looks_like_rejection_close(reply_body):
        apply_whatsapp_rejection_closeout(prospect)

    from app.services import whatsapp_assisted_service as was

    if was.whatsapp_manual_queue_enabled():
        was.mark_draft_suggested(db, prospect, campaign, reply_body, log_event=True)
    else:
        was.stash_inbound_reply_draft(prospect, reply_body)

    mode = _inbound_reply_mode(campaign)
    meow_row = _meow_session_for_campaign(db, campaign, session_row=meow_session_row)
    can_meow = allow_meow_send and _meow_session_usable(campaign, meow_row)
    auto_send_allowed = (
        not campaign_paused
        and mode == InboundReplyMode.auto_send.value
        and (
            _truthy_env("NEXUS_AUTO_SEND_ENABLED")
            or (allow_meow_send and _meow_inbound_auto_send_enabled())
        )
    )
    want_auto_send = auto_send_allowed and (allow_meta_send or allow_meow_send)
    meow_auto_send = (
        want_auto_send
        and allow_meow_send
        and can_meow
        and _meow_inbound_auto_send_enabled()
    )
    cloud_ok = allow_meta_send and is_whatsapp_cloud_api_enabled()
    can_deliver = can_meow or cloud_ok
    force_draft = (not want_auto_send) or (not can_deliver)
    if not meow_auto_send and allow_meow_send and want_auto_send:
        force_draft = True
    if (
        sig is not None
        and want_auto_send
        and can_deliver
        and not meow_auto_send
    ):
        force_draft = force_draft or iar.should_force_draft_only(sig, body)

    from app.services.whatsapp_humanize import inbound_reply_delay_seconds

    humanize_wait = inbound_reply_delay_seconds() > 0
    send_now = force_immediate or not humanize_wait

    logger.info(
        "[wa_auto_reply] deliver decision prospect_id=%s campaign_id=%s mode=%s "
        "allow_meta_send=%s allow_meow_send=%s can_meow=%s want_auto_send=%s "
        "meow_auto_send=%s cloud=%s force_immediate=%s force_draft=%s send_now=%s",
        prospect.id,
        campaign.id,
        mode,
        allow_meta_send,
        allow_meow_send,
        can_meow,
        want_auto_send,
        meow_auto_send,
        cloud_ok,
        force_immediate,
        force_draft,
        send_now,
    )

    if (
        meow_auto_send
        and not force_draft
        and meow_row is not None
        and send_now
    ):
        return _execute_meow_auto_reply_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            session_row=meow_row,
            inbound_id=mid,
            reply_body=reply_body,
        )

    if want_auto_send and can_deliver and not force_draft and not send_now:
        task_id = iar.schedule_inbound_auto_send(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_gmail_message_id=mid,
            channel="whatsapp",
            planned={"body": reply_body},
        )
        if task_id is not None:
            park_draft_for_auto_send(prospect, reply_body)
            return "scheduled"
        return "scheduled_duplicate"

    if want_auto_send and can_deliver and not force_draft and send_now:
        if meow_auto_send and meow_row is not None:
            return _execute_meow_auto_reply_delivery(
                db,
                campaign=campaign,
                prospect=prospect,
                session_row=meow_row,
                inbound_id=mid,
                reply_body=reply_body,
            )
        if cloud_ok:
            return _execute_whatsapp_delivery(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_id=mid,
                reply_body=reply_body,
                delivery="send",
            )

    delivery: Literal["draft", "send"] = (
        "send" if want_auto_send and cloud_ok and not force_draft else "draft"
    )
    return _execute_whatsapp_delivery(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_id=mid,
        reply_body=reply_body,
        delivery=delivery,
    )
