"""
Prospectos por campaña. El bulk está preparado para la futura extensión Chrome (LinkedIn),
reutilizando los mismos esquemas que el alta manual y la deduplicación en servidor.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_campaign, get_prospect
from app.models import Campaign
from app.models.enums import PipelineStage, ProspectStatus
from app.models.prospect import Prospect
from app.schemas.prospect import (
    ProspectBulkCreate,
    ProspectCreate,
    ProspectRead,
    ProspectSimulateRequest,
    ProspectUpdate,
)
from app.services import campaign_icp
from app.services import prospect_channels
from app.services import prospect_ingestion as ingestion
from app.services import prospect_scoring as scoring
from app.services import pipeline_sync
from app.services import multichannel_sequence
from app.services import outreach_metrics as om

_VALID_PIPELINE = {s.value for s in PipelineStage}

router = APIRouter(tags=["prospects"])


def _icp_placeholder(value: str | None) -> bool:
    return not value or campaign_icp.is_icp_token_empty(value)


def _serialize(p: Prospect, campaign: Campaign | None = None) -> ProspectRead:
    pc = getattr(p, "preferred_channel", None)
    cr = getattr(p, "channel_reason", None)
    if campaign is not None and (not pc or not cr):
        ch, rr = prospect_channels.compute_preferred_channel(p, campaign)
        pc, cr = pc or ch, cr or rr
    return ProspectRead(
        id=p.id,
        company_id=p.company_id,
        campaign_id=p.campaign_id,
        name=p.name,
        company_name=p.company_name,
        role=p.role,
        industry=p.industry,
        country=p.country,
        linkedin_url=p.linkedin_url,
        email=p.email,
        phone=p.phone,
        whatsapp=getattr(p, "whatsapp", None),
        company_website=getattr(p, "company_website", None),
        source_provider=getattr(p, "source_provider", None),
        source_external_id=getattr(p, "source_external_id", None),
        status=ProspectStatus(p.status),
        compatibility_score=p.compatibility_score,
        interest_probability=p.interest_probability,
        notes=p.notes,
        outreach_touch_count=int(getattr(p, "outreach_touch_count", 0) or 0),
        last_outbound_at=getattr(p, "last_outbound_at", None),
        last_inbound_at=getattr(p, "last_inbound_at", None),
        objection_type=getattr(p, "objection_type", None),
        objection_detected_at=getattr(p, "objection_detected_at", None),
        interest_level=getattr(p, "interest_level", None) or "low",
        meeting_nudge_sent_at=getattr(p, "meeting_nudge_sent_at", None),
        followup_count=int(getattr(p, "followup_count", 0) or 0),
        last_followup_at=getattr(p, "last_followup_at", None),
        score_reason=getattr(p, "score_reason", None),
        next_best_action=getattr(p, "next_best_action", None),
        pipeline_stage=getattr(p, "pipeline_stage", None) or "nuevo",
        meeting_suggestion_pending=bool(getattr(p, "meeting_suggestion_pending", False)),
        preferred_channel=pc,
        channel_reason=cr,
        linkedin_assisted_draft=getattr(p, "linkedin_assisted_draft", None),
        linkedin_assist_status=getattr(p, "linkedin_assist_status", None),
        linkedin_assist_session_id=getattr(p, "linkedin_assist_session_id", None),
        linkedin_last_assisted_at=getattr(p, "linkedin_last_assisted_at", None),
        linkedin_sdr_marked_sent_at=getattr(p, "linkedin_sdr_marked_sent_at", None),
        sequence_started_at=getattr(p, "sequence_started_at", None),
        sequence_group=getattr(p, "sequence_group", None) or "contactado",
        sequence_state=getattr(p, "sequence_state", None) or "sin_respuesta",
        sequence_fired_milestones=getattr(p, "sequence_fired_milestones", None) or "[]",
        sequence_paused=bool(getattr(p, "sequence_paused", False)),
        reactivation_sent_at=getattr(p, "reactivation_sent_at", None),
        defer_resume_at=getattr(p, "defer_resume_at", None),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _scores_for_campaign(campaign: Campaign, fields: dict) -> tuple[int, int, ProspectStatus, str]:
    compat, interest, st = scoring.score_prospect_against_campaign(
        fields,
        campaign_country=campaign.target_country,
        campaign_industry=campaign.target_industry,
        campaign_role=campaign.target_role,
    )
    _, fit_reason = scoring.explain_compatibility(
        fields,
        campaign_country=campaign.target_country,
        campaign_industry=campaign.target_industry,
        campaign_role=campaign.target_role,
        product_name=campaign.product.name if campaign.product else "Nexus Sales",
    )
    return compat, interest, st, f"fit inicial: {fit_reason}"


def _persist_new_prospect(db: Session, campaign: Campaign, payload: ProspectCreate) -> Prospect:
    dup = ingestion.find_duplicate_in_campaign(
        db,
        campaign_id=campaign.id,
        linkedin_url=payload.linkedin_url,
        name=payload.name,
        company_name=payload.company_name,
        email=payload.email,
        source_external_id=payload.source_external_id,
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail="Ya existe un prospecto duplicado en esta campaña (LinkedIn o nombre + empresa sin LinkedIn).",
        )

    fields = {
        "country": payload.country,
        "industry": payload.industry,
        "role": payload.role,
        "email": payload.email,
        "linkedin_url": payload.linkedin_url,
    }
    compat, interest, st, score_reason = _scores_for_campaign(campaign, fields)

    row = Prospect(
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        name=payload.name.strip(),
        company_name=payload.company_name.strip(),
        role=payload.role.strip() if payload.role else None,
        industry=payload.industry.strip() if payload.industry else None,
        country=payload.country.strip() if payload.country else None,
        linkedin_url=ingestion.normalize_linkedin_url(payload.linkedin_url),
        email=payload.email.strip() if payload.email else None,
        phone=payload.phone.strip() if payload.phone else None,
        whatsapp=payload.whatsapp.strip() if payload.whatsapp else None,
        company_website=payload.company_website.strip() if payload.company_website else None,
        source_provider=payload.source_provider.strip() if payload.source_provider else None,
        source_external_id=payload.source_external_id.strip() if payload.source_external_id else None,
        notes=payload.notes.strip() if payload.notes else None,
        status=st.value,
        compatibility_score=compat,
        interest_probability=interest,
        score_reason=score_reason,
        next_best_action="Iniciar outreach personalizado." if st == ProspectStatus.compatible else "Revisar fit ICP.",
    )
    db.add(row)
    db.flush()
    ch, rr = prospect_channels.compute_preferred_channel(row, campaign)
    row.preferred_channel = ch
    row.channel_reason = rr
    return row


@router.get("/campaigns/{campaign_id}/prospects", response_model=list[ProspectRead])
def list_campaign_prospects(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> list[ProspectRead]:
    rows = db.scalars(
        select(Prospect)
        .where(Prospect.campaign_id == campaign_id)
        .order_by(Prospect.created_at.desc())
    ).all()
    return [_serialize(r, campaign) for r in rows]


@router.post("/campaigns/{campaign_id}/prospects", response_model=ProspectRead, status_code=201)
def create_campaign_prospect(
    campaign_id: int,
    payload: ProspectCreate,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> ProspectRead:
    row = _persist_new_prospect(db, campaign, payload)
    db.commit()
    db.refresh(row)
    return _serialize(row, campaign)


@router.post("/campaigns/{campaign_id}/prospects/bulk", response_model=list[ProspectRead], status_code=201)
def bulk_create_prospects(
    campaign_id: int,
    body: ProspectBulkCreate,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> list[ProspectRead]:
    """
    Carga masiva idéntica a POST simple × N — pensada para pegar lotes desde la UI,
    pipelines internos y, más adelante, la extensión que envíe perfiles desde LinkedIn.
    """
    created: list[Prospect] = []
    try:
        for idx, item in enumerate(body.prospects):
            try:
                row = _persist_new_prospect(db, campaign, item)
                created.append(row)
            except HTTPException as exc:
                if exc.status_code == 409:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Duplicado en ítem #{idx + 1}: {exc.detail}",
                    ) from exc
                raise
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    for row in created:
        db.refresh(row)
    return [_serialize(r, campaign) for r in created]


def _slug_company(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")[:24]


def _simulate_templates(campaign: Campaign, count: int) -> list[ProspectCreate]:
    names = ["Sofía Ríos", "Lucas Paz", "Marina Costa", "Diego Vera", "Ana Lagos", "Tomás Mey"]
    firms = ["Andes Analytics", "Pampa Digital", "Cóndor Systems", "Litoral SaaS"]
    roles = ["Head of Sales", "Revenue Ops", "SDR Lead", "Founder"]
    fallback_industry = ["Software", "Fintech", "Logística"]

    demo: list[ProspectCreate] = []
    base_c = campaign.target_country
    base_i = campaign.target_industry

    for i in range(count):
        name = names[i % len(names)] + f" {i % 97}"
        co = firms[i % len(firms)]
        role = roles[i % len(roles)]
        industry = (
            base_i.strip()
            if base_i and not _icp_placeholder(base_i)
            else fallback_industry[i % len(fallback_industry)]
        )
        align = i % 3 != 1
        if base_c and not _icp_placeholder(base_c) and align:
            country = base_c.strip()
        elif i % 4 == 0:
            country = "Argentina"
        elif i % 4 == 1:
            country = "Chile"
        elif i % 4 == 2:
            country = "México"
        else:
            country = "España"

        first = name.split()[0].lower()
        slug = _slug_company(co)
        li_url = f"https://www.linkedin.com/in/demo-{campaign.id}-{i}-{first}-{slug}"
        demo.append(
            ProspectCreate(
                name=name.strip(),
                company_name=co,
                role=role,
                industry=industry,
                country=country,
                linkedin_url=li_url,
                email=f"demo.prospect.{campaign.id}.{i}@mail.nexus-sales.local",
                phone=f"+1 555 01{i % 100:02d}",
                notes="Generado en simulación local (fase 4).",
            )
        )
    return demo


@router.post("/campaigns/{campaign_id}/prospects/simulate", response_model=list[ProspectRead], status_code=201)
def simulate_prospects(
    campaign_id: int,
    payload: ProspectSimulateRequest,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> list[ProspectRead]:
    if om.is_outreach_simulation_disabled():
        raise HTTPException(
            status_code=403,
            detail="Simulación de prospectos deshabilitada (NEXUS_REAL_MODE o NEXUS_DISABLE_OUTREACH_SIMULATION).",
        )
    templates = _simulate_templates(campaign, payload.count)
    created: list[Prospect] = []
    try:
        for item in templates:
            row = _persist_new_prospect(db, campaign, item)
            created.append(row)
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    for row in created:
        db.refresh(row)
    return [_serialize(r, campaign) for r in created]


@router.patch("/prospects/{prospect_id}", response_model=ProspectRead)
def update_prospect(
    payload: ProspectUpdate,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectRead:
    raw_fields = payload.model_dump(exclude_unset=True)
    recalc = bool(raw_fields.pop("recalculate_scores", False))
    stage_manual = "pipeline_stage" in raw_fields
    meeting_manual = "meeting_suggestion_pending" in raw_fields
    data = {k: v for k, v in raw_fields.items()}
    pls = data.pop("pipeline_stage", None)
    msp = data.pop("meeting_suggestion_pending", None)
    status_touched = "status" in data and data["status"] is not None

    if "name" in data and data["name"] is not None:
        prospect.name = str(data["name"]).strip()
    if "company_name" in data and data["company_name"] is not None:
        prospect.company_name = str(data["company_name"]).strip()
    if "role" in data:
        prospect.role = str(data["role"]).strip() if data["role"] else None
    if "industry" in data:
        prospect.industry = str(data["industry"]).strip() if data["industry"] else None
    if "country" in data:
        prospect.country = str(data["country"]).strip() if data["country"] else None
    if "linkedin_url" in data:
        prospect.linkedin_url = ingestion.normalize_linkedin_url(
            str(data["linkedin_url"]) if data["linkedin_url"] else None
        )
    if "email" in data:
        prospect.email = str(data["email"]).strip() if data["email"] else None
    if "phone" in data:
        prospect.phone = str(data["phone"]).strip() if data["phone"] else None
    if "whatsapp" in data:
        prospect.whatsapp = str(data["whatsapp"]).strip() if data["whatsapp"] else None
    if "company_website" in data:
        prospect.company_website = (
            str(data["company_website"]).strip() if data["company_website"] else None
        )
    if "notes" in data:
        prospect.notes = str(data["notes"]).strip() if data["notes"] else None

    if "campaign_id" in data and data["campaign_id"] is not None:
        new_campaign_id = int(data["campaign_id"])
        campaign = db.get(Campaign, new_campaign_id)
        if campaign is None or campaign.company_id != prospect.company_id:
            raise HTTPException(status_code=400, detail="Campaña inválida para este prospecto")
        prospect.campaign_id = new_campaign_id

    if "status" in data and data["status"] is not None:
        st = data["status"]
        prospect.status = st.value if isinstance(st, ProspectStatus) else str(st)

    if stage_manual and pls is not None:
        pv = pls.value if isinstance(pls, PipelineStage) else str(pls)
        if pv not in _VALID_PIPELINE:
            raise HTTPException(status_code=400, detail="pipeline_stage inválido")
        prospect.pipeline_stage = pv
    if meeting_manual:
        prospect.meeting_suggestion_pending = bool(msp)

    db.flush()

    if recalc:
        campaign = db.get(Campaign, prospect.campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaña no encontrada")
        dup = ingestion.find_duplicate_in_campaign(
            db,
            campaign_id=campaign.id,
            linkedin_url=prospect.linkedin_url,
            name=prospect.name,
            company_name=prospect.company_name,
        )
        if dup is not None and dup.id != prospect.id:
            raise HTTPException(
                status_code=409,
                detail="El prospecto editado coincide con otro ya existente en la campaña.",
            )
        fields = {
            "country": prospect.country,
            "industry": prospect.industry,
            "role": prospect.role,
            "email": prospect.email,
            "linkedin_url": prospect.linkedin_url,
        }
        compat, interest, st, score_reason = _scores_for_campaign(campaign, fields)
        prospect.compatibility_score = compat
        prospect.interest_probability = interest
        prospect.status = st.value
        prospect.score_reason = score_reason
        pipeline_sync.sync_pipeline_from_status(prospect)

    elif status_touched and not stage_manual:
        pipeline_sync.sync_pipeline_from_status(prospect)

    camp_local = db.get(Campaign, prospect.campaign_id)
    if camp_local:
        ch, rr = prospect_channels.compute_preferred_channel(prospect, camp_local)
        prospect.preferred_channel = ch
        prospect.channel_reason = rr

    db.commit()
    db.refresh(prospect)
    return _serialize(prospect, camp_local)


@router.post("/prospects/{prospect_id}/sequence/reactivate", response_model=ProspectRead)
def reactivate_prospect_sequence(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectRead:
    """SDR: sacar de Encajonados o Postergados y volver a seguimiento automático."""
    camp = db.get(Campaign, prospect.campaign_id)
    if camp is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    g = (getattr(prospect, "sequence_group", None) or "").lower()
    if g == multichannel_sequence.SEQUENCE_GROUP_ENCAJONADO:
        multichannel_sequence.reactivate_from_encajonado(db, prospect, camp)
    elif g == multichannel_sequence.SEQUENCE_GROUP_POSTERGADO:
        multichannel_sequence.reactivate_from_postergado(db, prospect, camp)
    elif g == multichannel_sequence.SEQUENCE_GROUP_REUNIONES:
        multichannel_sequence.resume_from_reuniones(db, prospect, camp)
    else:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede reactivar desde Encajonados, Postergados o Reuniones.",
        )
    db.commit()
    db.refresh(prospect)
    return _serialize(prospect, camp)


@router.delete("/prospects/{prospect_id}", status_code=204)
def delete_prospect(
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> Response:
    db.delete(prospect)
    db.commit()
    return Response(status_code=204)
