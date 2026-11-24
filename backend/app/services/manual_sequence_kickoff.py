"""Kickoff de secuencia individual (sin activar campaña de outreach)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import is_company_admin
from app.core.sequence_playbook import is_touch_calendar_due
from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, OutreachEmailMode
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services import prospect_sequence as seq
from app.services.credits import CreditError, consume_sequence_individual_credit

logger = logging.getLogger(__name__)

# Contenedor de secuencias individuales (una por empresa). Visible en Campañas.
INDIVIDUAL_CAMPAIGN_NAME = "Secuencias individuales"
# Prefijo legacy (por producto); se migra al nombre canónico.
INDIVIDUAL_CAMPAIGN_PREFIX = "Nexus · Secuencias individuales"
INDIVIDUAL_DEFAULT_CHANNELS = ["linkedin", "email", "whatsapp"]


def _now() -> datetime:
    return datetime.now(UTC)


def _humanize_kickoff_failure(detail: str) -> str:
    """Mensaje corto para la UI/actividad: nunca jerga de compose/crédito."""
    raw = (detail or "").strip()
    low = raw.lower()
    if "asunto" in low or "cuerpo" in low or "compose" in low:
        return "No se pudo armar el primer mensaje. Reintentá en un momento."
    if (
        "invalid_grant" in low
        or "revoc" in low
        or (
            ("gmail" in low or "google" in low)
            and ("conect" in low or "oauth" in low or "access blocked" in low or "desconect" in low)
        )
    ):
        return "Google está desconectado. Conectalo de nuevo en Integraciones."
    if "whatsapp" in low and ("sesión" in low or "meow" in low or "conect" in low):
        return "WhatsApp no está conectado. Escaneá el QR e intentá de nuevo."
    if "mismo número conectado" in low or "no el tuyo" in low:
        return (
            "El contacto tiene tu mismo WhatsApp. Poné el número del prospecto "
            "(no el tuyo) e iniciá de nuevo."
        )
    if "crédito" in low or "credito" in low or "saldo" in low:
        return "No hay créditos suficientes para iniciar la secuencia."
    # Sacá prefijos técnicos que ya metimos nosotros.
    for prefix in (
        "El primer toque no se completó: ",
        "No se descontó el crédito.",
        " No se descontó el crédito.",
    ):
        raw = raw.replace(prefix, "").strip()
    if len(raw) > 160:
        return "No se pudo iniciar la secuencia. Revisá el contacto e intentá de nuevo."
    return raw or "No se pudo iniciar la secuencia. Reintentá en un momento."


def _plan_channels_ordered(campaign: Campaign) -> list[str]:
    """Canales del plan de secuencia (orden del playbook). Vacío → defaults."""
    plan = getattr(campaign, "sequence_plan", None)
    if not isinstance(plan, dict):
        return []
    out: list[str] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        ch = str(step.get("channel") or "").strip().lower()
        if ch in ("wa", "phone"):
            ch = "whatsapp"
        if ch in ("whatsapp", "email", "linkedin", "call") and ch not in out:
            out.append(ch)
    return out


def _kickoff_channel_priority(campaign: Campaign) -> list[str]:
    """Playbook primero; si falta dato, otros canales con dato — salvo plan solo WhatsApp.

    Plan WA-only no debe caer a email (compose vacío → error confuso y crédito trabado).
    Plan LI-only sí puede caer a WhatsApp si hay número.
    """
    planned = _plan_channels_ordered(campaign)
    if not planned:
        return ["whatsapp", "linkedin", "email"]
    if planned == ["whatsapp"]:
        return planned
    rest = [c for c in ("whatsapp", "linkedin", "email") if c not in planned]
    return planned + rest


def _user_has_gmail(db: Session, *, company_id: int, user_id: int) -> bool:
    try:
        from app.services.gmail_drafts import get_valid_gmail_connection

        get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def try_find_gmail_operator(
    db: Session, *, company_id: int, preferred: User | None = None
) -> User | None:
    """Elige un usuario de la empresa con Gmail OAuth válido, o None."""
    candidates: list[User] = []
    if preferred is not None and preferred.company_id == company_id:
        candidates.append(preferred)
    others = db.scalars(
        select(User).where(User.company_id == company_id, User.is_active.is_(True))
    ).all()
    for u in others:
        if preferred is None or u.id != preferred.id:
            candidates.append(u)
    for u in candidates:
        if _user_has_gmail(db, company_id=company_id, user_id=u.id):
            return u
    return None


def find_gmail_operator(db: Session, *, company_id: int, preferred: User | None = None) -> User:
    """Elige un usuario de la empresa con Gmail OAuth válido."""
    found = try_find_gmail_operator(db, company_id=company_id, preferred=preferred)
    if found is not None:
        return found
    raise HTTPException(
        status_code=400,
        detail=(
            "No hay ninguna cuenta Google/Gmail válida en la empresa para enviar. "
            "CostGuard tiene que reconectar Gmail en el servidor antes de poder mandar."
        ),
    )


def find_sequence_operator(db: Session, *, company_id: int, preferred: User | None = None) -> User:
    """Operador de secuencia: preferí Gmail válido; si no hay, el preferred / primer activo."""
    try:
        return find_gmail_operator(db, company_id=company_id, preferred=preferred)
    except HTTPException:
        pass
    if preferred is not None and preferred.company_id == company_id and preferred.is_active:
        return preferred
    others = db.scalars(
        select(User).where(User.company_id == company_id, User.is_active.is_(True))
    ).all()
    if preferred is not None:
        for u in others:
            if u.id == preferred.id:
                return u
    if others:
        return others[0]
    raise HTTPException(
        status_code=400,
        detail="No hay usuarios activos en la empresa para operar la secuencia.",
    )


def is_individual_container_campaign(campaign: Campaign | None) -> bool:
    if campaign is None:
        return False
    name = str(campaign.name or "").strip()
    return name == INDIVIDUAL_CAMPAIGN_NAME or name.startswith(INDIVIDUAL_CAMPAIGN_PREFIX)


def _channels_from_sequence_plan(plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(plan, dict):
        return []
    out: list[str] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        ch = str(step.get("channel") or "").strip().lower()
        if ch in ("wa", "phone"):
            ch = "whatsapp"
        if ch in ("linkedin", "email", "whatsapp") and ch not in out:
            out.append(ch)
    return out


def ensure_individual_allowed_channels(
    campaign: Campaign,
    plan: dict[str, Any] | None = None,
) -> Campaign:
    """El contenedor individual siempre admite LI/email/WA + los canales del plan."""
    if not is_individual_container_campaign(campaign):
        return campaign
    from app.schemas.campaign_channels import coerce_allowed_channels

    chosen = plan if isinstance(plan, dict) else getattr(campaign, "sequence_plan", None)
    merged: list[str] = []
    for ch in (
        coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
        + _channels_from_sequence_plan(chosen if isinstance(chosen, dict) else None)
        + list(INDIVIDUAL_DEFAULT_CHANNELS)
    ):
        lc = "whatsapp" if str(ch).strip().lower() in ("wa", "phone") else str(ch).strip().lower()
        if lc in ("linkedin", "email", "whatsapp") and lc not in merged:
            merged.append(lc)
    campaign.allowed_channels = merged or list(INDIVIDUAL_DEFAULT_CHANNELS)
    return campaign


def get_or_create_individual_container(
    db: Session,
    *,
    company_id: int,
    product: Product,
    operator: User,
    bind_product: bool = True,
) -> Campaign:
    """
    Una secuencia individual por empresa (visible en Campañas).
    Queda en ready; no dispara búsqueda ICP.

    ``bind_product=True`` (insert manual): fija el producto elegido para vender.
    ``bind_product=False`` (ensure al listar campañas / seed): NO pisa el producto —
    si no, el primer Product de la empresa (p. ej. Nexus Sales) vuelve a quedar
    pegado y el copy vende Nexus aunque el SDR eligió Contractia.
    """
    from app.services.campaign_prospects import count_campaign_prospects
    from app.services.prospect_timezone import operator_timezone

    operator_tz = operator_timezone(user=operator)

    existing = db.scalars(
        select(Campaign)
        .where(
            Campaign.company_id == company_id,
            Campaign.name == INDIVIDUAL_CAMPAIGN_NAME,
        )
        .order_by(Campaign.id.asc())
    ).first()
    if existing is None:
        # Migrar contenedor legacy (por producto) si existe.
        legacy = db.scalars(
            select(Campaign)
            .where(
                Campaign.company_id == company_id,
                Campaign.name.startswith(INDIVIDUAL_CAMPAIGN_PREFIX),
            )
            .order_by(Campaign.id.asc())
        ).first()
        if legacy is not None:
            legacy.name = INDIVIDUAL_CAMPAIGN_NAME
            existing = legacy

    if existing is not None:
        # No reasignar el vendedor: si no, las secuencias de un SDR aparecen en la bandeja de otro.
        if not existing.seller_id:
            existing.seller_id = operator.id
        if bind_product:
            existing.product_id = product.id
        elif not existing.product_id:
            existing.product_id = product.id
        existing.automation_paused = False
        if (existing.status or "").lower() not in ("ready", "running"):
            existing.status = CampaignStatus.ready.value
        existing.outreach_email_mode = OutreachEmailMode.auto_send.value
        existing.timezone = operator_tz
        ensure_individual_allowed_channels(existing)
        imported = count_campaign_prospects(db, existing.id)
        if int(existing.prospect_count or 0) > imported + 5:
            existing.prospect_count = max(imported, 1)
        db.flush()
        return existing

    row = Campaign(
        company_id=company_id,
        seller_id=operator.id,
        product_id=product.id,
        name=INDIVIDUAL_CAMPAIGN_NAME,
        prospect_count=1,
        calendar_link="https://calendar.google.com/calendar/u/0/r",
        timezone=operator_tz,
        available_hours="Lun-Vie 09:00-18:00",
        tone="profesional y directo",
        target_country="Argentina",
        status=CampaignStatus.ready.value,
        automation_paused=False,
        outreach_email_mode=OutreachEmailMode.auto_send.value,
        inbound_reply_mode="auto_send",
        allowed_channels=["linkedin", "email", "whatsapp"],
        estimated_meetings_min=0,
        estimated_meetings_max=0,
        estimated_cost_min=0,
        estimated_cost_max=0,
        estimated_avg_cost_per_meeting=0.0,
    )
    db.add(row)
    db.flush()
    return row


def ensure_individual_container_for_company(
    db: Session,
    *,
    company_id: int,
) -> Campaign | None:
    """
    Garantiza el contenedor «Secuencias individuales» por empresa.
    Visible siempre en Campañas; no se puede eliminar.
    """
    from app.models.product import Product

    product = db.scalars(
        select(Product)
        .where(Product.company_id == company_id, Product.is_active.is_(True))
        .order_by(Product.id.asc())
    ).first()
    if product is None:
        product = db.scalars(
            select(Product).where(Product.company_id == company_id).order_by(Product.id.asc())
        ).first()
    if product is None:
        return None

    preferred = db.scalars(
        select(User)
        .where(User.company_id == company_id, User.is_active.is_(True))
        .order_by(User.id.asc())
    ).first()
    if preferred is None:
        return None

    operator = find_sequence_operator(db, company_id=company_id, preferred=preferred)
    return get_or_create_individual_container(
        db,
        company_id=company_id,
        product=product,
        operator=operator,
        bind_product=False,
    )


def _touch_counts_as_delivered(result: dict[str, Any]) -> bool:
    """Primer toque listo: Gmail enviado, LI/WA en cola, o WhatsApp nativo/API enviado."""
    return bool(
        result.get("gmail_sent")
        or result.get("linkedin_assisted")
        or result.get("whatsapp_assisted")
        or result.get("whatsapp_sent")
    )


def _prospect_has_delivered_sequence_touch(prospect: Prospect, campaign: Campaign | None) -> bool:
    log = seq._touch_log(prospect)
    for day in seq._planned_days(prospect, campaign):
        status = str((log.get(str(day), {}) or {}).get("status") or "").strip().lower()
        if status in ("enviado", "respondido"):
            return True
    return False


def _already_started_payload(prospect: Prospect, campaign: Campaign) -> dict[str, Any]:
    return {
        "prospect_id": prospect.id,
        "campaign_id": campaign.id,
        "product_id": campaign.product_id,
        "already_started": True,
        "credit_consumed": 0,
        "gmail_sent": False,
        "linkedin_assisted": False,
        "whatsapp_assisted": False,
        "day1_channel": None,
        "first_delivered_day": None,
        "waiting_calendar": False,
        "day1": None,
        "notes": ["La secuencia ya estaba iniciada. No se volvió a ejecutar ni se descontó crédito."],
        "message": "La secuencia ya estaba iniciada.",
    }


def _undo_failed_individual_start(
    db: Session, *, prospect_id: int, company_id: int, actor_id: int, charged: bool
) -> bool:
    """No salió ningún toque de este ciclo: vuelve a 'sin arrancar'.

    Siempre limpia ``sequence_started_at`` si el touch log no tiene enviado/respondido;
    si no, un fallo (p. ej. sin número + outbound viejo) deja al prospecto trabado y
    «Iniciar secuencia» no vuelve a encolar kickoff.
    Devuelve True solo si se devolvió el crédito.
    """
    from app.services.credits import release_user_credits

    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return False

    campaign = db.get(Campaign, int(prospect.campaign_id)) if prospect.campaign_id else None
    if _prospect_has_delivered_sequence_touch(prospect, campaign):
        logger.warning(
            "kickoff undo skipped: ya hay toque entregado prospect=%s",
            prospect_id,
        )
        return False

    from app.services.operational_safety import (
        prospect_has_confirmed_gmail_outbound,
        prospect_has_whatsapp_sequence_outbound,
    )

    gmail_confirmed = prospect_has_confirmed_gmail_outbound(db, prospect_id)
    if gmail_confirmed:
        # Mail con ID Gmail ya salió: no borrar el start ni devolver crédito.
        logger.info(
            "kickoff undo skipped: Gmail outbound confirmado prospect=%s",
            prospect_id,
        )
        return False

    had_outbound = prospect_has_whatsapp_sequence_outbound(db, prospect_id)
    if had_outbound:
        logger.info(
            "kickoff undo: reset sin devolver crédito (hubo outbound WA previo) prospect=%s",
            prospect_id,
        )

    # Sin Gmail confirmado: liberar started_at para poder reintentar kickoff.
    prospect.sequence_started_at = None
    prospect.next_touch_at = None
    prospect.sequence_touch_log = None
    prospect.sequence_fired_milestones = "[]"
    prospect.sequence_paused = False
    refunded = False
    if charged and not had_outbound:
        release_user_credits(
            db,
            company_id,
            actor_id,
            1,
            reason="secuencia individual no arrancó (devolución)",
            actor_user_id=actor_id,
        )
        refunded = True
    db.commit()
    return refunded


def kickoff_individual_sequence_for_prospect(
    db: Session,
    *,
    actor: User,
    campaign: Campaign,
    prospect: Prospect,
    wait_for_enrich: bool = True,
) -> dict[str, Any]:
    """
    Start individual + 1 crédito + primer toque entregable.
    No cambia el status de campañas de outreach normales a running.

    Si faltan canales del plan: espera la búsqueda (hasta deadline) y arranca
    con lo conseguido. Día 1 omitido por canal faltante → intenta el siguiente toque.
    """
    notes: list[str] = []
    company_id = int(campaign.company_id)

    from app.services.meeting_booking import prospect_meeting_blocks_new_sequence

    meeting_block = prospect_meeting_blocks_new_sequence(prospect, db=db)
    if meeting_block:
        raise HTTPException(status_code=400, detail=meeting_block)

    resuming = False
    if prospect.sequence_started_at is not None:
        if _prospect_has_delivered_sequence_touch(prospect, campaign):
            return _already_started_payload(prospect, campaign)
        if not is_individual_container_campaign(campaign):
            return _already_started_payload(prospect, campaign)
        resuming = True
        notes.append("Retomando el primer toque: el arranque anterior no llegó a enviar.")

    if wait_for_enrich:
        try:
            from app.services.manual_channel_enrich_job import (
                STATUS_SEARCHING,
                wait_until_enrich_settled,
            )

            if (prospect.channel_enrich_status or "").strip().lower() == STATUS_SEARCHING:
                settled = wait_until_enrich_settled(db, prospect)
                notes.append(f"Búsqueda de datos: {settled}.")
        except Exception as exc:  # noqa: BLE001
            logger.info("wait enrich skipped prospect=%s: %s", prospect.id, exc)

    # Completar canales que el plan pide y aún faltan (ancla = LinkedIn / nombre+empresa).
    # timed_out / searching / none: igual intentamos Prospeo una vez si faltan datos.
    # G: listas/CRM no reintentan Prospeo en kickoff.
    enrich_status = (prospect.channel_enrich_status or "").strip().lower()
    try:
        from app.services.manual_channel_enrich_job import is_list_or_crm_source

        if is_list_or_crm_source(prospect):
            prospect.channel_enrich_status = "skipped"
            enrich_status = "skipped"
            db.flush()
    except Exception:  # noqa: BLE001
        pass
    if enrich_status not in ("done", "skipped"):
        try:
            from app.services.manual_prospect_channel_enrichment import (
                enrich_prospect_for_sequence_plan,
            )

            enrich = enrich_prospect_for_sequence_plan(
                db,
                prospect,
                sequence_plan=getattr(campaign, "sequence_plan", None),
                # Tras la espera: un intento final sin cortar por deadline viejo.
                deadline_at=None,
            )
            filled = enrich.get("filled") or {}
            if filled:
                bits = [k for k in ("email", "linkedin", "phone") if k in filled]
                if bits:
                    notes.append(f"Canales completados al iniciar: {', '.join(bits)}.")
            still = enrich.get("missing_after") or []
            if still:
                notes.append(
                    "Sin dato externo para: "
                    + ", ".join(still)
                    + " (esos toques se omiten si faltan)."
                )
            if enrich.get("timed_out"):
                prospect.channel_enrich_status = "timed_out"
            else:
                prospect.channel_enrich_status = "done"
            db.flush()
        except Exception as exc:  # noqa: BLE001
            logger.info("individual enrich skipped prospect=%s: %s", prospect.id, exc)
    else:
        msg = (prospect.channel_enrich_message or "").strip()
        if msg:
            notes.append(msg)

    # Operador: Gmail solo si el primer canal *disponible* es email.
    plan = getattr(campaign, "sequence_plan", None)
    first_ready_ch = None
    individual = is_individual_container_campaign(campaign)
    if individual:
        ensure_individual_allowed_channels(
            campaign, plan if isinstance(plan, dict) else None
        )
    if isinstance(plan, dict) and plan.get("steps"):
        from app.services.prospect_sequence import _channel_ready

        for step in sorted(plan["steps"], key=lambda s: int(s.get("day") or 0)):
            if not isinstance(step, dict):
                continue
            ch = str(step.get("channel") or "").strip().lower()
            if ch and _channel_ready(
                prospect,
                "whatsapp" if ch in ("wa", "phone", "whatsapp") else ch,
            ):
                first_ready_ch = "whatsapp" if ch in ("wa", "phone", "whatsapp") else ch
                break
    # Secuencia individual: quien inicia opera y toma el prospecto (no forzar otro Gmail).
    if individual and actor.company_id == company_id and actor.is_active:
        operator = actor
    elif first_ready_ch == "email":
        operator = find_gmail_operator(db, company_id=company_id, preferred=actor)
    else:
        operator = find_sequence_operator(db, company_id=company_id, preferred=actor)

    # Contenedor individual: seller = quien tiene Gmail. Campañas normales: no tocar status.
    from app.services.operational_safety import maybe_unpause_campaign_for_kickoff

    if is_individual_container_campaign(campaign):
        campaign.seller_id = operator.id
        maybe_unpause_campaign_for_kickoff(db, campaign, company_id)
        campaign.outreach_email_mode = OutreachEmailMode.auto_send.value
        if (campaign.status or "").lower() not in ("ready", "running"):
            campaign.status = CampaignStatus.ready.value
    else:
        maybe_unpause_campaign_for_kickoff(db, campaign, company_id)

    from app.services.outreach_display_names import sender_first_name

    sender = sender_first_name(user=operator, campaign_sender=getattr(campaign, "sender_name", None))
    if sender:
        campaign.sender_name = sender
    db.flush()
    if prospect.owner_user_id and prospect.owner_user_id != operator.id:
        may_take = (
            is_company_admin(actor.role)
            or prospect.owner_user_id == actor.id
            or (individual and int(operator.id) == int(actor.id))
        )
        if not may_take:
            raise HTTPException(status_code=403, detail="Este prospecto está tomado por otro usuario.")
        prospect.previous_owner_user_id = prospect.owner_user_id

    prospect.owner_user_id = operator.id
    prospect.claimed_at = _now()
    prospect.ownership_status = "tomado"
    prospect.ownership_cooldown_until = None
    db.flush()

    # Scaffold sin OpenAI: el mensaje real se genera UNA sola vez en execute (evita timeout).
    from app.models.product import Product

    product = db.get(Product, int(campaign.product_id)) if campaign.product_id else None
    seq.bootstrap_sequence_scaffold_fast(
        db,
        prospect=prospect,
        campaign=campaign,
        product=product,
    )
    db.refresh(prospect)
    operator = db.get(User, operator.id) or operator
    campaign = db.get(Campaign, campaign.id) or campaign

    from app.services.operational_safety import (
        prospect_has_confirmed_gmail_outbound,
        prospect_has_whatsapp_sequence_outbound,
        try_claim_sequence_start,
    )

    db.refresh(prospect)
    if prospect.sequence_started_at is None and (
        prospect_has_whatsapp_sequence_outbound(db, int(prospect.id))
        or prospect_has_confirmed_gmail_outbound(db, int(prospect.id))
    ):
        from app.services.commercial_conversation_state import bump_sequence_send_cycle

        bump_sequence_send_cycle(prospect)
        db.flush()

    charged = False
    if resuming:
        pass
    elif prospect.sequence_started_at is not None or not try_claim_sequence_start(db, prospect):
        if _prospect_has_delivered_sequence_touch(prospect, campaign):
            return _already_started_payload(prospect, campaign)
        resuming = True
        notes.append("Retomando el primer toque: el arranque anterior no llegó a enviar.")
    else:
        # 1 crédito al arrancar (antes del primer toque). Sin saldo → no inicia.
        # En campañas normales la persona ya se pagó al reservar la campaña.
        charged = is_individual_container_campaign(campaign)
    if charged:
        try:
            consume_sequence_individual_credit(
                db,
                company_id,
                int(actor.id),
                actor_user_id=int(actor.id),
            )
            db.commit()
            db.refresh(prospect)
        except CreditError as exc:
            prospect.sequence_started_at = None
            db.flush()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        notes.append("1 crédito descontado por secuencia individual.")

    seq.start_prospect_sequence(
        db, user=operator, prospect=prospect, continuation_after_claim=True
    )
    db.refresh(prospect)

    planned_days = list(seq._planned_days(prospect, campaign))
    if not planned_days:
        planned_days = [1]

    day1_result: dict[str, Any] | None = None
    delivered_day: int | None = None
    waiting_calendar = False
    skipped_channels: list[str] = []

    def _run_touch(day_i: int, *, channel_override: str | None = None) -> dict[str, Any]:
        return seq.execute_sequence_touch(
            db,
            user=operator,
            prospect=prospect,
            day=day_i,
            scheduled=False,
            channel_override=channel_override,
        )

    def _accept_touch(day_i: int, result: dict[str, Any]) -> bool:
        nonlocal day1_result, delivered_day
        day1_result = result
        if result.get("gmail_draft_created") and not (
            result.get("gmail_sent")
            or result.get("linkedin_assisted")
            or result.get("whatsapp_assisted")
            or result.get("whatsapp_sent")
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Se creó borrador en Gmail pero no se envió. "
                    "Revisá outreach_email_mode=auto_send y NEXUS_AUTO_SEND_ENABLED=1."
                ),
            )
        if (
            bool(result.get("gmail_sent"))
            or bool(result.get("linkedin_assisted"))
            or _touch_counts_as_delivered(result)
        ):
            delivered_day = day_i
            return True
        if result.get("omitted") or result.get("skipped"):
            notes.append(
                result.get("summary")
                or result.get("message")
                or f"Toque día {day_i} omitido; se intenta el siguiente canal disponible."
            )
            return False
        return False

    channel_priority = _kickoff_channel_priority(campaign)

    def _try_touch(day_i: int, *, channel_override: str | None = None) -> bool:
        """Ejecuta un toque; si falla un canal, no tumba todo el kickoff (sigue al siguiente)."""
        nonlocal day1_result
        try:
            result = _run_touch(day_i, channel_override=channel_override)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            ch = (channel_override or "").strip().lower() or "canal"
            logger.warning(
                "individual kickoff touch failed prospect=%s day=%s channel=%s: %s",
                prospect.id,
                day_i,
                ch,
                detail[:300],
            )
            notes.append(f"{ch}: no salió; probamos otro canal.")
            day1_result = {
                "omitted": True,
                "skipped": True,
                "channel": ch,
                "summary": _humanize_kickoff_failure(detail),
            }
            return False
        return _accept_touch(day_i, result)

    try:
        for day in planned_days:
            day_i = int(day)
            step = seq._playbook_step(day_i, campaign)
            channel = str(getattr(step, "channel", "") or "").strip().lower() if step else ""
            ready = bool(channel and seq._channel_ready(prospect, channel))
            # Sin dato de ese canal: se omite y se busca el primero que sí se pueda mandar.
            if channel and not ready:
                skipped_channels.append(f"{channel} día {day_i}")
                logger.info(
                    "individual kickoff skip prospect=%s day=%s channel=%s reason=sin_dato",
                    prospect.id,
                    day_i,
                    channel,
                )
                if day_i not in seq._completed_days(prospect, campaign):
                    seq._auto_omit_sequence_touch(
                        db,
                        prospect=prospect,
                        day=day_i,
                        reason=f"{channel}_sin_dato",
                    )
                    db.flush()
                    notes.append(f"Día {day_i} ({channel}) sin dato; se omite.")
                continue
            # Individual: el primer canal con dato sale ahora (no esperar al día 10).
            if not is_touch_calendar_due(prospect.sequence_started_at, day_i):
                if individual and ready:
                    notes.append(
                        f"Día {day_i} ({channel}) adelantado: es el primer canal con dato."
                    )
                elif individual:
                    continue
                else:
                    next_at, _ = seq.compute_next_touch(prospect, campaign)
                    prospect.next_touch_at = next_at
                    waiting_calendar = True
                    notes.append(
                        f"Día {day_i} queda para el calendario de secuencia "
                        f"({next_at.isoformat() if next_at else 'próximo hito'})."
                    )
                    break
            if _try_touch(day_i):
                break
            # Falló ese canal: en individual seguimos buscando; no cortar el kickoff.
            if not individual:
                break
        if delivered_day is None and individual and not waiting_calendar:
            fallback_day = int(planned_days[0]) if planned_days else 1
            for ch in channel_priority:
                if not seq._channel_ready(prospect, ch):
                    continue
                notes.append(f"Arranque por {ch}: es el canal con dato disponible.")
                logger.info(
                    "individual kickoff fallback prospect=%s day=%s channel=%s",
                    prospect.id,
                    fallback_day,
                    ch,
                )
                seq._set_touch_entry(
                    prospect,
                    fallback_day,
                    status=seq.TOUCH_GENERADO,
                    skip_reason=None,
                    error=None,
                    channel=ch,
                )
                db.flush()
                if _try_touch(fallback_day, channel_override=ch):
                    break
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        logger.warning("individual kickoff day1 failed prospect=%s: %s", prospect.id, detail[:300])
        if delivered_day is None:
            refunded = _undo_failed_individual_start(
                db, prospect_id=int(prospect.id), company_id=company_id, actor_id=int(actor.id), charged=charged
            )
            human = _humanize_kickoff_failure(detail)
            if refunded:
                human = f"{human} No se usó el crédito."
            raise HTTPException(status_code=exc.status_code, detail=human) from exc
        raise

    if delivered_day is None and not waiting_calendar:
        ready_now = [ch for ch in channel_priority if seq._channel_ready(prospect, ch)]
        if not ready_now:
            detail = "Falta un WhatsApp, LinkedIn o email válido para arrancar."
        elif any(ch == "whatsapp" for ch in _plan_channels_ordered(campaign)) and not seq._channel_ready(
            prospect, "whatsapp"
        ):
            detail = "El plan arranca por WhatsApp y este contacto no tiene un número usable."
        else:
            detail = "No se pudo enviar el primer mensaje. Reintentá en un momento."
        refunded = _undo_failed_individual_start(
            db, prospect_id=int(prospect.id), company_id=company_id, actor_id=int(actor.id), charged=charged
        )
        human = _humanize_kickoff_failure(detail)
        if refunded:
            human = f"{human} No se usó el crédito."
        raise HTTPException(status_code=400, detail=human)

    if waiting_calendar and delivered_day is None:
        notes.append(
            "Secuencia iniciada; el próximo canal se encola cuando toque el día del playbook."
        )
    elif delivered_day and delivered_day != 1:
        notes.append(f"Primer envío en día {delivered_day} (canales previos sin dato).")

    day1_channel = str((day1_result or {}).get("channel") or "").strip().lower()
    if day1_result and delivered_day is not None:
        if day1_result.get("gmail_sent"):
            notes.append("Email: enviado ahora por Gmail. La secuencia sigue sola.")
        elif day1_result.get("linkedin_assisted"):
            notes.append("LinkedIn: en cola (verificando conexión o listo para enviar).")
        elif day1_result.get("whatsapp_assisted"):
            notes.append("WhatsApp: en cola para envío manual.")
        elif day1_result.get("whatsapp_sent"):
            notes.append("WhatsApp: enviado (nativo o API).")
        else:
            notes.append(day1_result.get("message") or "Primer toque ejecutado.")

    db.refresh(prospect)
    return {
        "prospect_id": prospect.id,
        "campaign_id": campaign.id,
        "product_id": campaign.product_id,
        "operator_user_id": operator.id,
        "credit_consumed": 1 if charged else 0,
        "gmail_sent": bool((day1_result or {}).get("gmail_sent")),
        "linkedin_assisted": bool((day1_result or {}).get("linkedin_assisted")),
        "whatsapp_assisted": bool((day1_result or {}).get("whatsapp_assisted")),
        "whatsapp_sent": bool((day1_result or {}).get("whatsapp_sent")),
        "day1_channel": day1_channel or None,
        "first_delivered_day": delivered_day,
        "waiting_calendar": waiting_calendar and delivered_day is None,
        "day1": day1_result,
        "notes": notes,
        "message": " ".join(notes),
    }
