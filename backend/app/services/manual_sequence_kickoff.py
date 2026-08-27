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


def _now() -> datetime:
    return datetime.now(UTC)


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


def get_or_create_individual_container(
    db: Session,
    *,
    company_id: int,
    product: Product,
    operator: User,
) -> Campaign:
    """
    Una secuencia individual por empresa (visible en Campañas).
    Queda en ready; no dispara búsqueda ICP.
    """
    from app.services.campaign_prospects import count_campaign_prospects

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
        existing.product_id = product.id
        existing.automation_paused = False
        if (existing.status or "").lower() not in ("ready", "running"):
            existing.status = CampaignStatus.ready.value
        existing.outreach_email_mode = OutreachEmailMode.auto_send.value
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
        timezone="America/Argentina/Buenos_Aires",
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
    )


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
    enrich_status = (prospect.channel_enrich_status or "").strip().lower()
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
    if first_ready_ch == "email":
        operator = find_gmail_operator(db, company_id=company_id, preferred=actor)
    else:
        operator = find_sequence_operator(db, company_id=company_id, preferred=actor)

    # Contenedor individual: seller = quien tiene Gmail. Campañas normales: no tocar status.
    if is_individual_container_campaign(campaign):
        campaign.seller_id = operator.id
        campaign.automation_paused = False
        campaign.outreach_email_mode = OutreachEmailMode.auto_send.value
        if (campaign.status or "").lower() not in ("ready", "running"):
            campaign.status = CampaignStatus.ready.value
    else:
        # Compat: si alguien llama con campaña normal, no la “arrancamos”.
        campaign.automation_paused = False

    from app.services.outreach_display_names import sender_first_name

    sender = sender_first_name(user=operator, campaign_sender=getattr(campaign, "sender_name", None))
    if sender:
        campaign.sender_name = sender
    db.flush()
    if prospect.owner_user_id and prospect.owner_user_id != operator.id:
        if not is_company_admin(actor.role) and prospect.owner_user_id != actor.id:
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

    # 1 crédito al arrancar (antes del primer toque). Sin saldo → no inicia.
    try:
        consume_sequence_individual_credit(
            db,
            company_id,
            int(actor.id),
            actor_user_id=int(actor.id),
        )
    except CreditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    notes.append("1 crédito descontado por secuencia individual.")

    seq.start_prospect_sequence(db, user=operator, prospect=prospect)
    db.refresh(prospect)

    planned_days = list(seq._planned_days(prospect, campaign))
    if not planned_days:
        planned_days = [1]

    day1_result: dict[str, Any] | None = None
    delivered_day: int | None = None
    waiting_calendar = False
    try:
        for day in planned_days:
            day_i = int(day)
            # Foco D: kickoff manual tampoco adelanta días futuros del playbook.
            if not is_touch_calendar_due(prospect.sequence_started_at, day_i):
                next_at, _ = seq.compute_next_touch(prospect, campaign)
                prospect.next_touch_at = next_at
                waiting_calendar = True
                notes.append(
                    f"Día {day_i} queda para el calendario de secuencia "
                    f"({next_at.isoformat() if next_at else 'próximo hito'})."
                )
                break
            result = seq.execute_sequence_touch(
                db,
                user=operator,
                prospect=prospect,
                day=day_i,
                scheduled=False,
            )
            day1_result = result
            if result.get("gmail_draft_created") and not (
                result.get("gmail_sent")
                or result.get("linkedin_assisted")
                or result.get("whatsapp_assisted")
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Se creó borrador en Gmail pero no se envió. "
                        "Revisá outreach_email_mode=auto_send y NEXUS_AUTO_SEND_ENABLED=1."
                    ),
                )
            mail_ok = bool(result.get("gmail_sent"))
            li_ok = bool(result.get("linkedin_assisted"))
            wa_ok = bool(result.get("whatsapp_assisted"))
            if mail_ok or li_ok or wa_ok:
                delivered_day = day_i
                break
            if result.get("omitted") or result.get("skipped"):
                notes.append(
                    result.get("summary")
                    or result.get("message")
                    or f"Toque día {day} omitido; se intenta el siguiente canal disponible."
                )
                continue
            # Fallo duro no-omit: cortar
            break
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        notes.append(f"Secuencia armada, pero el primer toque falló: {detail}")
        logger.warning("individual kickoff day1 failed prospect=%s: %s", prospect.id, detail[:300])
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"El toque 1 no se completó (el crédito ya se descontó al arrancar): {detail}",
        ) from exc

    if delivered_day is None and not waiting_calendar:
        if day1_result is None:
            raise HTTPException(status_code=400, detail="No se pudo ejecutar ningún toque de la secuencia.")
        raise HTTPException(
            status_code=400,
            detail=(
                day1_result.get("summary")
                or day1_result.get("message")
                or "Ningún canal disponible para arrancar (email, LinkedIn o WhatsApp)."
            ),
        )

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
        else:
            notes.append(day1_result.get("message") or "Primer toque ejecutado.")

    db.refresh(prospect)
    return {
        "prospect_id": prospect.id,
        "campaign_id": campaign.id,
        "product_id": campaign.product_id,
        "operator_user_id": operator.id,
        "credit_consumed": 1,
        "gmail_sent": bool((day1_result or {}).get("gmail_sent")),
        "linkedin_assisted": bool((day1_result or {}).get("linkedin_assisted")),
        "whatsapp_assisted": bool((day1_result or {}).get("whatsapp_assisted")),
        "day1_channel": day1_channel or None,
        "first_delivered_day": delivered_day,
        "waiting_calendar": waiting_calendar and delivered_day is None,
        "day1": day1_result,
        "notes": notes,
        "message": " ".join(notes),
    }
