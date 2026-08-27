from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.deps import get_campaign, get_company
from app.models.campaign import Campaign
from app.models.enums import (
    AutopilotStatus,
    CampaignStatus,
    InboundReplyMode,
    MarketScope,
    OutreachEmailMode,
    OutreachMode,
    UserRole,
)
from pydantic import BaseModel

from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.schemas.campaign import (
    CampaignCreate,
    CampaignIcpAnalysisRead,
    CampaignRead,
    CampaignUpdate,
    ProspectEstimateRequest,
    ProspectEstimateResponse,
)
from app.schemas.autopilot import AutopilotCycleRead
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services import campaign_icp as icp
from app.services import campaign_autopilot
from app.services import openai_service
from app.services.campaign_estimation import estimate_campaign_metrics
from app.services.campaign_market import (
    normalize_outreach_mode,
    product_market_scope,
    resolve_outreach_mode,
)
from app.services.campaign_prospects import count_campaign_prospects
from app.services.credits import (
    CreditError,
    adjust_campaign_prospection_credits,
    get_user_available_credits,
    release_user_credits,
    reserve_campaign_prospection_credits,
)

router = APIRouter(tags=["campaigns"])


def _strip_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    t = str(value).strip()
    return t or None

def _outreach_email_mode_row(row: Campaign) -> OutreachEmailMode:
    raw = getattr(row, "outreach_email_mode", None) or OutreachEmailMode.draft_only.value
    try:
        return OutreachEmailMode(str(raw))
    except ValueError:
        return OutreachEmailMode.draft_only


def _inbound_reply_mode_row(row: Campaign) -> InboundReplyMode:
    raw = getattr(row, "inbound_reply_mode", None) or InboundReplyMode.draft_only.value
    try:
        return InboundReplyMode(str(raw))
    except ValueError:
        return InboundReplyMode.draft_only


def _inbound_reply_delay_row(row: Campaign) -> int:
    raw = int(getattr(row, "inbound_reply_delay_minutes", None) or 2)
    if raw in (1, 2, 5):
        return raw
    return 2


def _activity_log_safe(row: Campaign) -> list | None:
    raw = getattr(row, "outreach_activity_log", None)
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    return None


def _campaign_channels(row: Campaign) -> list[str]:
    return coerce_allowed_channels(getattr(row, "allowed_channels", None))


def _product_market_scope_enum(product: Product | None) -> MarketScope | None:
    if product is None:
        return None
    try:
        return MarketScope(product_market_scope(product))
    except ValueError:
        return MarketScope.b2b


def _outreach_mode_row(row: Campaign) -> OutreachMode:
    try:
        return OutreachMode(normalize_outreach_mode(getattr(row, "outreach_mode", None)))
    except ValueError:
        return OutreachMode.b2b


def _serialize_campaign(row: Campaign) -> CampaignRead:
    product_name = row.product.name if row.product else "Producto desconocido"
    seller_name = row.seller.name if row.seller else "Vendedor desconocido"
    status = CampaignStatus(row.status)

    return CampaignRead(
        id=row.id,
        company_id=row.company_id,
        seller_id=row.seller_id,
        product_id=row.product_id,
        product_name=product_name,
        seller_name=seller_name,
        name=row.name,
        status=status,
        autopilot_status=AutopilotStatus(getattr(row, "autopilot_status", "off")),
        autopilot_last_cycle_at=getattr(row, "autopilot_last_cycle_at", None),
        autopilot_last_cycle_summary=getattr(row, "autopilot_last_cycle_summary", None),
        outreach_activity_log=_activity_log_safe(row),
        outreach_mode=_outreach_mode_row(row),
        product_market_scope=_product_market_scope_enum(row.product),
        target_company_size=row.target_company_size,
        target_industry=row.target_industry,
        target_country=row.target_country,
        target_language=row.target_language,
        target_role=row.target_role,
        target_area=getattr(row, "target_area", None),
        target_interests=getattr(row, "target_interests", None),
        prospect_count=row.prospect_count,
        calendar_link=row.calendar_link,
        timezone=row.timezone,
        available_hours=row.available_hours,
        tone=row.tone,
        allowed_channels=_campaign_channels(row),
        estimated_meetings_min=row.estimated_meetings_min,
        estimated_meetings_max=row.estimated_meetings_max,
        estimated_cost_min=row.estimated_cost_min,
        estimated_cost_max=row.estimated_cost_max,
        estimated_avg_cost_per_meeting=float(row.estimated_avg_cost_per_meeting),
        created_at=row.created_at,
        updated_at=getattr(row, "updated_at", None),
        sender_name=getattr(row, "sender_name", None),
        sender_email=getattr(row, "sender_email", None),
        ai_context=getattr(row, "ai_context", None),
        followup_delay_days=getattr(row, "followup_delay_days", None),
        max_auto_followups=getattr(row, "max_auto_followups", None),
        post_sequence_followup_enabled=bool(
            getattr(row, "post_sequence_followup_enabled", True)
        ),
        outreach_email_mode=_outreach_email_mode_row(row),
        inbound_reply_mode=_inbound_reply_mode_row(row),
        inbound_reply_delay_minutes=_inbound_reply_delay_row(row),
        automation_paused=bool(getattr(row, "automation_paused", False)),
        icp_ai_last_analysis=getattr(row, "icp_ai_last_analysis", None)
        if isinstance(getattr(row, "icp_ai_last_analysis", None), dict)
        else None,
        sequence_plan=getattr(row, "sequence_plan", None)
        if isinstance(getattr(row, "sequence_plan", None), dict)
        else None,
    )


def _is_campaign_assignable_role(role: str | None) -> bool:
    raw = (role or "").strip().lower()
    return raw in (
        UserRole.sdr.value,
        UserRole.manager.value,
        UserRole.gerente.value,
        UserRole.owner.value,
        "seller",
        "director",
    )


def _validate_relations(db: Session, company_id: int, seller_id: int, product_id: int) -> None:
    seller = db.get(User, seller_id)
    if seller is None or seller.company_id != company_id:
        raise HTTPException(status_code=400, detail="Vendedor no válido para esta empresa")
    if not _is_campaign_assignable_role(seller.role):
        raise HTTPException(
            status_code=400,
            detail="La campaña debe asignarse a un SDR, Manager o Director de la empresa",
        )

    product = db.get(Product, product_id)
    if product is None or product.company_id != company_id:
        raise HTTPException(status_code=400, detail="Producto no válido para esta empresa")
    if not product.is_active:
        raise HTTPException(status_code=400, detail="El producto debe estar activo")


def _apply_estimates(db: Session, campaign: Campaign) -> None:
    metrics = estimate_campaign_metrics(campaign.prospect_count)
    campaign.estimated_meetings_min = int(metrics["estimated_meetings_min"])
    campaign.estimated_meetings_max = int(metrics["estimated_meetings_max"])
    campaign.estimated_cost_min = int(metrics["estimated_cost_min"])
    campaign.estimated_cost_max = int(metrics["estimated_cost_max"])
    campaign.estimated_avg_cost_per_meeting = float(metrics["estimated_avg_cost_per_meeting"])
    db.flush()


@router.post("/companies/{company_id}/campaigns/preview-estimates", response_model=ProspectEstimateResponse)
def preview_campaign_estimates(
    company_id: int,
    payload: ProspectEstimateRequest,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> ProspectEstimateResponse:
    try:
        metrics = estimate_campaign_metrics(payload.prospect_count)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return ProspectEstimateResponse(prospect_count=payload.prospect_count, **metrics)


@router.get("/companies/{company_id}/campaigns", response_model=list[CampaignRead])
def list_company_campaigns(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> list[CampaignRead]:
    import logging
    import time

    from fastapi import HTTPException
    from sqlalchemy.exc import OperationalError

    log = logging.getLogger("nexus.http")
    t0 = time.perf_counter()
    log.info("[campaigns] list_company_campaigns company_id=%s query_start", company_id)
    try:
        from app.services.manual_sequence_kickoff import ensure_individual_container_for_company

        ensure_individual_container_for_company(db, company_id=company_id)
        db.commit()
        rows = db.scalars(
            select(Campaign)
            .where(Campaign.company_id == company_id)
            .options(selectinload(Campaign.product), selectinload(Campaign.seller))
            .order_by(Campaign.created_at.desc())
        ).all()
    except OperationalError as exc:
        log.exception(
            "[campaigns] list_company_campaigns sqlite_busy company_id=%s elapsed_ms=%s",
            company_id,
            int((time.perf_counter() - t0) * 1000),
        )
        raise HTTPException(status_code=503, detail="Base de datos ocupada. Reintentá.") from exc

    def _include_campaign(r: Campaign) -> bool:
        from app.services.manual_sequence_kickoff import (
            INDIVIDUAL_CAMPAIGN_NAME,
            INDIVIDUAL_CAMPAIGN_PREFIX,
        )

        name = str(r.name or "").strip()
        if name == INDIVIDUAL_CAMPAIGN_NAME:
            return True
        # Ocultar duplicados legacy por producto; el canónico ya está migrado/creado.
        if name.startswith(INDIVIDUAL_CAMPAIGN_PREFIX):
            return False
        return True

    from app.services.campaign_visibility import campaign_is_visible_to_user

    out = [
        _serialize_campaign(r)
        for r in rows
        if _include_campaign(r) and campaign_is_visible_to_user(user, r)
    ]
    log.info(
        "[campaigns] list_company_campaigns company_id=%s count=%s elapsed_ms=%s",
        company_id,
        len(out),
        int((time.perf_counter() - t0) * 1000),
    )
    return out


@router.post("/companies/{company_id}/campaigns", response_model=CampaignRead, status_code=201)
def create_campaign(
    company_id: int,
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> CampaignRead:
    product = db.get(Product, payload.product_id)
    requested_mode = payload.outreach_mode.value if payload.outreach_mode else None
    outreach_mode = resolve_outreach_mode(product=product, requested=requested_mode)

    ts = icp.normalize_optional_icp_field(payload.target_company_size)
    ti = icp.normalize_optional_icp_field(payload.target_industry)
    tc = icp.normalize_optional_icp_field(payload.target_country)
    tl = icp.normalize_optional_icp_field(payload.target_language)
    tr = icp.normalize_optional_icp_field(payload.target_role)
    ta = icp.normalize_optional_icp_field(payload.target_area)
    tint = icp.normalize_optional_icp_field(payload.target_interests)

    try:
        icp.assert_icp_has_signal(
            target_company_size=ts,
            target_industry=ti,
            target_country=tc,
            target_language=tl,
            target_role=tr,
            target_area=ta,
            target_interests=tint,
            outreach_mode=outreach_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    _validate_relations(db, company_id, payload.seller_id, payload.product_id)

    available_credits = get_user_available_credits(db, company_id, payload.seller_id)
    if payload.prospect_count > available_credits:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Las prospecciones elegidas ({payload.prospect_count}) superan tus créditos "
                f"disponibles ({available_credits}). 1 crédito = 1 persona en secuencia completa."
            ),
        )

    status_val = payload.status.value
    campaign_name = payload.name.strip()

    from app.services.available_hours import validate_available_hours_text

    hours_err = validate_available_hours_text(payload.available_hours)
    if hours_err:
        raise HTTPException(status_code=422, detail=hours_err)

    try:
        reserve_campaign_prospection_credits(
            db,
            company_id,
            payload.seller_id,
            payload.prospect_count,
            campaign_name=campaign_name,
        )
    except CreditError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    campaign = Campaign(
        company_id=company_id,
        seller_id=payload.seller_id,
        product_id=payload.product_id,
        name=campaign_name,
        status=status_val,
        autopilot_status=AutopilotStatus.off.value,
        outreach_mode=outreach_mode,
        target_company_size=ts,
        target_industry=ti,
        target_country=tc,
        target_language=tl,
        target_role=tr,
        target_area=ta,
        target_interests=tint,
        prospect_count=payload.prospect_count,
        calendar_link=payload.calendar_link.strip(),
        timezone=payload.timezone.strip(),
        available_hours=payload.available_hours.strip(),
        tone=payload.tone.strip(),
        allowed_channels=payload.allowed_channels,
        estimated_meetings_min=0,
        estimated_meetings_max=0,
        estimated_cost_min=0,
        estimated_cost_max=0,
        estimated_avg_cost_per_meeting=0.0,
        sender_name=_strip_optional_str(payload.sender_name),
        sender_email=_strip_optional_str(payload.sender_email),
        ai_context=_strip_optional_str(payload.ai_context),
        followup_delay_days=payload.followup_delay_days,
        max_auto_followups=payload.max_auto_followups,
        post_sequence_followup_enabled=payload.post_sequence_followup_enabled,
        outreach_email_mode=payload.outreach_email_mode.value,
        automation_paused=payload.automation_paused,
        inbound_reply_mode=payload.inbound_reply_mode.value,
        inbound_reply_delay_minutes=payload.inbound_reply_delay_minutes,
        sequence_plan=payload.sequence_plan,
    )
    db.add(campaign)
    db.flush()
    _apply_estimates(db, campaign)
    db.commit()
    db.refresh(campaign)

    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign.id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller))
    ).first()
    if campaign is None:
        raise HTTPException(status_code=500, detail="Campaña inconsistente")
    return _serialize_campaign(campaign)


@router.get("/campaigns/{campaign_id}", response_model=CampaignRead)
def get_campaign_detail(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> CampaignRead:
    campaign_loaded = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller))
    ).first()
    if campaign_loaded is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    return _serialize_campaign(campaign_loaded)


@router.post("/campaigns/{campaign_id}/analyze-icp", response_model=CampaignIcpAnalysisRead)
def analyze_campaign_icp_with_ai(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> CampaignIcpAnalysisRead:
    campaign_loaded = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign_loaded is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    product = campaign_loaded.product
    pname = product.name if product else "Producto"
    pdesc = ""
    if product:
        pdesc = f"{product.description or ''}\n{product.value_proposition or ''}".strip()
    analysis = openai_service.analyze_campaign_icp(
        campaign_name=campaign_loaded.name,
        product_name=pname,
        product_description=pdesc,
        target_company_size=campaign_loaded.target_company_size,
        target_industry=campaign_loaded.target_industry,
        target_country=campaign_loaded.target_country,
        target_language=campaign_loaded.target_language,
        target_role=campaign_loaded.target_role,
        target_interests=getattr(campaign_loaded, "target_interests", None),
        outreach_mode=normalize_outreach_mode(getattr(campaign_loaded, "outreach_mode", None)),
        tone=campaign_loaded.tone,
        allowed_channels=_campaign_channels(campaign_loaded),
        prospect_count=int(campaign_loaded.prospect_count),
    )
    campaign_loaded.icp_ai_last_analysis = dict(analysis)
    db.commit()
    return CampaignIcpAnalysisRead.model_validate(analysis)


@router.patch("/campaigns/{campaign_id}", response_model=CampaignRead)
def update_campaign(
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> CampaignRead:
    data = payload.model_dump(exclude_unset=True)

    seller_id = data.get("seller_id", campaign.seller_id)
    product_id = data.get("product_id", campaign.product_id)
    product = db.get(Product, product_id)
    if "outreach_mode" in data and data["outreach_mode"] is not None:
        requested_mode = (
            data["outreach_mode"].value
            if hasattr(data["outreach_mode"], "value")
            else str(data["outreach_mode"])
        )
    else:
        requested_mode = getattr(campaign, "outreach_mode", None)
    outreach_mode = resolve_outreach_mode(product=product, requested=requested_mode)

    merged_icp = {
        "target_company_size": icp.normalize_optional_icp_field(
            data["target_company_size"]
            if "target_company_size" in data
            else campaign.target_company_size
        ),
        "target_industry": icp.normalize_optional_icp_field(
            data["target_industry"] if "target_industry" in data else campaign.target_industry
        ),
        "target_country": icp.normalize_optional_icp_field(
            data["target_country"] if "target_country" in data else campaign.target_country
        ),
        "target_language": icp.normalize_optional_icp_field(
            data["target_language"] if "target_language" in data else campaign.target_language
        ),
        "target_role": icp.normalize_optional_icp_field(
            data["target_role"] if "target_role" in data else campaign.target_role
        ),
        "target_area": icp.normalize_optional_icp_field(
            data["target_area"] if "target_area" in data else getattr(campaign, "target_area", None)
        ),
        "target_interests": icp.normalize_optional_icp_field(
            data["target_interests"]
            if "target_interests" in data
            else getattr(campaign, "target_interests", None)
        ),
    }

    try:
        icp.assert_icp_has_signal(**merged_icp, outreach_mode=outreach_mode)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    _validate_relations(db, campaign.company_id, seller_id, product_id)

    if "name" in data and data["name"] is not None:
        campaign.name = str(data["name"]).strip()

    campaign.seller_id = seller_id
    campaign.product_id = product_id
    campaign.outreach_mode = outreach_mode

    campaign.target_company_size = merged_icp["target_company_size"]
    campaign.target_industry = merged_icp["target_industry"]
    campaign.target_country = merged_icp["target_country"]
    campaign.target_language = merged_icp["target_language"]
    campaign.target_role = merged_icp["target_role"]
    campaign.target_area = merged_icp["target_area"]
    campaign.target_interests = merged_icp["target_interests"]

    if "calendar_link" in data and data["calendar_link"] is not None:
        campaign.calendar_link = str(data["calendar_link"]).strip()
    if "timezone" in data and data["timezone"] is not None:
        campaign.timezone = str(data["timezone"]).strip()
    if "available_hours" in data and data["available_hours"] is not None:
        hours_raw = str(data["available_hours"]).strip()
        from app.services.available_hours import validate_available_hours_text

        hours_err = validate_available_hours_text(hours_raw)
        if hours_err:
            raise HTTPException(status_code=422, detail=hours_err)
        campaign.available_hours = hours_raw
    if "tone" in data and data["tone"] is not None:
        campaign.tone = str(data["tone"]).strip()

    if "allowed_channels" in data and data["allowed_channels"] is not None:
        campaign.allowed_channels = data["allowed_channels"]

    if "status" in data and data["status"] is not None:
        status_val = data["status"]
        campaign.status = (
            status_val.value if isinstance(status_val, CampaignStatus) else str(status_val)
        )

    if "autopilot_status" in data and data["autopilot_status"] is not None:
        ap = data["autopilot_status"]
        campaign.autopilot_status = ap.value if hasattr(ap, "value") else str(ap)

    if "sender_name" in data:
        campaign.sender_name = _strip_optional_str(data.get("sender_name"))
    if "sender_email" in data:
        campaign.sender_email = _strip_optional_str(data.get("sender_email"))
    if "ai_context" in data:
        campaign.ai_context = _strip_optional_str(data.get("ai_context"))
    if "followup_delay_days" in data:
        campaign.followup_delay_days = data["followup_delay_days"]
    if "max_auto_followups" in data:
        campaign.max_auto_followups = data["max_auto_followups"]
    if "post_sequence_followup_enabled" in data and data["post_sequence_followup_enabled"] is not None:
        enabled = bool(data["post_sequence_followup_enabled"])
        campaign.post_sequence_followup_enabled = enabled
        if not enabled:
            from app.services.followup_engine import cancel_campaign_pending_followup_tasks

            cancel_campaign_pending_followup_tasks(db, campaign.id)
            campaign.followup_delay_days = None

    if "outreach_email_mode" in data and data["outreach_email_mode"] is not None:
        v = data["outreach_email_mode"]
        campaign.outreach_email_mode = v.value if hasattr(v, "value") else str(v)
    if "automation_paused" in data and data["automation_paused"] is not None:
        campaign.automation_paused = bool(data["automation_paused"])
    if "inbound_reply_mode" in data and data["inbound_reply_mode"] is not None:
        v = data["inbound_reply_mode"]
        campaign.inbound_reply_mode = v.value if hasattr(v, "value") else str(v)
    if "inbound_reply_delay_minutes" in data and data["inbound_reply_delay_minutes"] is not None:
        campaign.inbound_reply_delay_minutes = int(data["inbound_reply_delay_minutes"])

    if "sequence_plan" in data:
        campaign.sequence_plan = data.get("sequence_plan")

    recompute = False
    if "prospect_count" in data and data["prospect_count"] is not None:
        new_count = int(data["prospect_count"])
        old_count = int(campaign.prospect_count)
        imported = count_campaign_prospects(db, campaign.id)
        if new_count < imported:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No podés bajar las prospecciones por debajo de los {imported} contactos "
                    "ya importados en esta campaña."
                ),
            )
        if new_count != old_count:
            try:
                adjust_campaign_prospection_credits(
                    db,
                    campaign.company_id,
                    campaign.seller_id,
                    old_count,
                    new_count,
                    campaign_name=campaign.name,
                )
            except CreditError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            campaign.prospect_count = new_count
            recompute = True

    db.flush()

    if recompute:
        _apply_estimates(db, campaign)

    campaign.updated_at = datetime.now(UTC)

    db.commit()

    reloaded = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign.id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller))
    ).first()
    if reloaded is None:
        raise HTTPException(status_code=500, detail="Campaña inconsistente")
    return _serialize_campaign(reloaded)


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> Response:
    from sqlalchemy import delete as sa_delete
    from sqlalchemy.exc import IntegrityError

    from app.models.ai_decision_event import AiDecisionEvent
    from app.models.crm_sync_event import CrmSyncEvent
    from app.models.inbound_auto_reply_receipt import InboundAutoReplyReceipt
    from app.models.lead_sourcing_pipeline import LeadSourcingPipeline
    from app.models.meeting import Meeting
    from app.models.outreach import OutreachMessage, OutreachSequence
    from app.models.outreach_task import OutreachTask
    from app.models.prospect import Prospect
    from app.models.prospect_ownership_event import ProspectOwnershipEvent
    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if is_individual_container_campaign(campaign):
        raise HTTPException(
            status_code=400,
            detail=(
                "La campaña «Secuencias individuales» no se puede eliminar. "
                "Es el contenedor permanente de secuencias fuera de campaña."
            ),
        )

    imported = count_campaign_prospects(db, campaign.id)
    unused = max(0, int(campaign.prospect_count) - imported)
    if unused > 0:
        release_user_credits(
            db,
            campaign.company_id,
            campaign.seller_id,
            unused,
            reason=f"eliminar campaña «{campaign.name}»",
        )

    prospect_ids = list(
        db.scalars(select(Prospect.id).where(Prospect.campaign_id == campaign.id)).all()
    )
    if prospect_ids:
        db.execute(
            sa_delete(ProspectOwnershipEvent).where(
                ProspectOwnershipEvent.prospect_id.in_(prospect_ids)
            )
        )
        db.execute(sa_delete(CrmSyncEvent).where(CrmSyncEvent.prospect_id.in_(prospect_ids)))
        db.execute(
            sa_delete(InboundAutoReplyReceipt).where(
                InboundAutoReplyReceipt.prospect_id.in_(prospect_ids)
            )
        )
        db.execute(
            sa_delete(AiDecisionEvent).where(AiDecisionEvent.prospect_id.in_(prospect_ids))
        )

    # Tablas ligadas a campaña (con o sin cascade ORM confiable en SQLite).
    db.execute(sa_delete(LeadSourcingPipeline).where(LeadSourcingPipeline.campaign_id == campaign.id))
    db.execute(
        sa_delete(InboundAutoReplyReceipt).where(InboundAutoReplyReceipt.campaign_id == campaign.id)
    )
    db.execute(sa_delete(AiDecisionEvent).where(AiDecisionEvent.campaign_id == campaign.id))
    db.execute(sa_delete(Meeting).where(Meeting.campaign_id == campaign.id))
    db.execute(sa_delete(OutreachTask).where(OutreachTask.campaign_id == campaign.id))
    db.execute(sa_delete(OutreachMessage).where(OutreachMessage.campaign_id == campaign.id))
    db.execute(sa_delete(OutreachSequence).where(OutreachSequence.campaign_id == campaign.id))
    db.execute(sa_delete(Prospect).where(Prospect.campaign_id == campaign.id))

    try:
        db.delete(campaign)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "No se pudo eliminar la campaña por datos relacionados. "
                "Reintentá o contactá soporte si persiste."
            ),
        ) from exc
    return Response(status_code=204)


@router.post("/campaigns/{campaign_id}/autopilot/activate", response_model=CampaignRead)
def activate_campaign_autopilot(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> CampaignRead:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    campaign.autopilot_status = AutopilotStatus.running.value
    db.commit()
    db.refresh(campaign)
    campaign_loaded = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller))
    ).first()
    if campaign_loaded is None:
        raise HTTPException(status_code=500, detail="Campaña inconsistente")
    return _serialize_campaign(campaign_loaded)


@router.post("/campaigns/{campaign_id}/autopilot/pause", response_model=CampaignRead)
def pause_campaign_autopilot(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> CampaignRead:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    campaign.autopilot_status = AutopilotStatus.paused.value
    db.commit()
    db.refresh(campaign)
    campaign_loaded = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller))
    ).first()
    if campaign_loaded is None:
        raise HTTPException(status_code=500, detail="Campaña inconsistente")
    return _serialize_campaign(campaign_loaded)


@router.post("/campaigns/{campaign_id}/autopilot/run-cycle", response_model=AutopilotCycleRead)
def run_campaign_autopilot_cycle(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> AutopilotCycleRead:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    if campaign.autopilot_status == AutopilotStatus.off.value:
        campaign.autopilot_status = AutopilotStatus.running.value
    stats, log = campaign_autopilot.run_campaign_cycle(db, campaign)
    if stats.processed > 0 and stats.messages_generated == 0 and stats.responses_simulated == 0:
        campaign.autopilot_status = AutopilotStatus.completed.value
    db.commit()
    db.refresh(campaign)
    return AutopilotCycleRead(
        campaign_id=campaign.id,
        autopilot_status=AutopilotStatus(campaign.autopilot_status),
        executed_at=campaign.autopilot_last_cycle_at or datetime.now(UTC),
        stats=stats,
        log=log,
    )


class CampaignReEnrichPhonesRead(BaseModel):
    campaign_id: int
    queued: int
    prospects_missing_phone: int
    message: str


@router.post("/campaigns/{campaign_id}/re-enrich-phones", response_model=CampaignReEnrichPhonesRead)
def re_enrich_campaign_phones(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> CampaignReEnrichPhonesRead:
    """Re-encola enrich-person para prospectos sin WhatsApp usable (móvil enmascarado o vacío)."""
    from app.services.campaign_sequence_channels import campaign_requires_whatsapp
    from app.services.manual_channel_enrich_job import (
        begin_manual_channel_enrich,
        schedule_manual_channel_enrich,
    )
    from app.services.manual_prospect_channel_enrichment import _missing_channels, _strip_masked_phones
    from app.services.whatsapp_cloud_service import sanitize_stored_phone

    if not campaign_requires_whatsapp(campaign):
        raise HTTPException(
            status_code=400,
            detail="La campaña no incluye WhatsApp en canales o secuencia.",
        )

    plan = campaign.sequence_plan if isinstance(campaign.sequence_plan, dict) else None
    rows = db.scalars(
        select(Prospect).where(Prospect.campaign_id == int(campaign_id))
    ).all()

    missing_count = 0
    queued_ids: list[int] = []
    for prospect in rows:
        _strip_masked_phones(prospect)
        phone = sanitize_stored_phone(prospect.phone) or sanitize_stored_phone(prospect.whatsapp)
        if phone:
            continue
        missing_count += 1
        missing = _missing_channels(prospect)
        if "phone" not in missing:
            continue
        meta = begin_manual_channel_enrich(db, prospect, sequence_plan=plan)
        if meta.get("enriching"):
            queued_ids.append(int(prospect.id))

    if queued_ids:
        db.commit()
        for pid in queued_ids:
            schedule_manual_channel_enrich(pid, kickoff_if_running=True)
    else:
        db.commit()

    msg = (
        f"Enriqueciendo {len(queued_ids)} contacto(s) para WhatsApp."
        if queued_ids
        else "Ningún prospecto pendiente de búsqueda de teléfono."
    )
    return CampaignReEnrichPhonesRead(
        campaign_id=int(campaign_id),
        queued=len(queued_ids),
        prospects_missing_phone=missing_count,
        message=msg,
    )
