"""
Prospectos por campaña. El bulk está preparado para la futura extensión Chrome (LinkedIn),
reutilizando los mismos esquemas que el alta manual y la deduplicación en servidor.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.auth.deps import get_current_user
from app.deps import get_campaign, get_prospect
from app.models import Campaign
from app.models.enums import CampaignStatus, PipelineStage, ProspectStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.schemas.prospect import (
    ProspectBulkCreate,
    ProspectCreate,
    ManualIndividualSequenceCreate,
    ProspectRead,
    ProspectSimulateRequest,
    ProspectUpdate,
)
from app.services import campaign_icp
from app.services import prospect_channels
from app.services import prospect_ingestion as ingestion
from app.services import prospect_scoring as scoring
from app.services.whatsapp_cloud_service import sanitize_stored_email, sanitize_stored_phone


def _clean_contact_phones(
    phone: str | None, whatsapp: str | None, landline: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """Móvil WA + fijo por separado (nunca previews enmascarados de Prospeo)."""
    from app.services.whatsapp_phone_validation import sanitize_landline_phone, sanitize_whatsapp_mobile

    mobile = sanitize_whatsapp_mobile(whatsapp) or sanitize_whatsapp_mobile(phone)
    landline_val = sanitize_landline_phone(landline)
    if not landline_val and phone and not mobile:
        landline_val = sanitize_landline_phone(phone)
    return mobile, mobile, landline_val
from app.services import pipeline_sync
from app.services import multichannel_sequence
from app.services import outreach_metrics as om

_VALID_PIPELINE = {s.value for s in PipelineStage}

router = APIRouter(tags=["prospects"])


def _icp_placeholder(value: str | None) -> bool:
    return not value or campaign_icp.is_icp_token_empty(value)


def _serialize(p: Prospect, campaign: Campaign | None = None, *, compact: bool = False) -> ProspectRead:
    from app.services.prospect_activity import compute_prospect_activity
    from app.services.prospect_icp_checklist import build_prospect_icp_checklist
    from app.services.manual_prospect_channel_enrichment import (
        channels_needed_from_sequence_plan,
        format_channel_find_summary,
    )

    pc = getattr(p, "preferred_channel", None)
    cr = getattr(p, "channel_reason", None)
    if (not compact) and campaign is not None and (not pc or not cr):
        ch, rr = prospect_channels.compute_preferred_channel(p, campaign)
        pc, cr = pc or ch, cr or rr
    activity = compute_prospect_activity(p)
    stored_msg = (getattr(p, "channel_enrich_message", None) or "").strip()
    enrich_st = (getattr(p, "channel_enrich_status", None) or "").strip().lower()
    if compact:
        find_summary = stored_msg or None
        icp_checklist = []
    else:
        plan = getattr(campaign, "sequence_plan", None) if campaign is not None else None
        needed = channels_needed_from_sequence_plan(plan if isinstance(plan, dict) else None)
        find_summary = format_channel_find_summary(
            needed=needed,
            prospect=p,
            enrich_status=getattr(p, "channel_enrich_status", None),
        )
        if enrich_st == "searching" and stored_msg:
            find_summary = stored_msg
        icp_checklist = build_prospect_icp_checklist(p, campaign)

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
        linkedin_profile_urn=(getattr(p, "linkedin_profile_urn", None) or None),
        email=p.email,
        phone=p.phone,
        whatsapp=getattr(p, "whatsapp", None),
        landline_phone=getattr(p, "landline_phone", None),
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
        icp_checklist=icp_checklist,
        next_best_action=getattr(p, "next_best_action", None),
        pipeline_stage=getattr(p, "pipeline_stage", None) or "nuevo",
        meeting_suggestion_pending=bool(getattr(p, "meeting_suggestion_pending", False)),
        preferred_channel=pc,
        channel_reason=cr,
        channel_enrich_status=getattr(p, "channel_enrich_status", None) or "none",
        channel_enrich_deadline_at=getattr(p, "channel_enrich_deadline_at", None),
        channel_enrich_message=getattr(p, "channel_enrich_message", None),
        channel_find_summary=find_summary,
        activity_code=str(activity.get("code") or "none"),
        activity_label=str(activity.get("label") or ""),
        activity_tone=str(activity.get("tone") or "muted"),
        linkedin_assisted_draft=getattr(p, "linkedin_assisted_draft", None),
        linkedin_assist_status=getattr(p, "linkedin_assist_status", None),
        linkedin_assist_session_id=getattr(p, "linkedin_assist_session_id", None),
        linkedin_last_assisted_at=getattr(p, "linkedin_last_assisted_at", None),
        linkedin_sdr_marked_sent_at=getattr(p, "linkedin_sdr_marked_sent_at", None),
        linkedin_connection_status=getattr(p, "linkedin_connection_status", None) or "none",
        linkedin_mention_next_touch=bool(getattr(p, "linkedin_mention_next_touch", False)),
        whatsapp_assist_status=getattr(p, "whatsapp_assist_status", None),
        whatsapp_assisted_draft=getattr(p, "whatsapp_assisted_draft", None),
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


from app.services.campaign_prospects import count_campaign_prospects


def _assert_campaign_prospect_slot(db: Session, campaign: Campaign) -> None:
    """Si el cupo está lleno, amplía +1 automáticamente (consume 1 crédito del seller)."""
    imported = count_campaign_prospects(db, campaign.id)
    target = int(campaign.prospect_count or 0)
    if imported < target:
        return

    from app.services.credits import CreditError, reserve_campaign_prospection_credits

    try:
        reserve_campaign_prospection_credits(
            db,
            int(campaign.company_id),
            int(campaign.seller_id),
            1,
            campaign_name=str(campaign.name or campaign.id),
        )
    except CreditError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Alcanzaste el límite de prospecciones de esta campaña ({target}) "
                f"y no hay créditos para ampliar el cupo: {exc}"
            ),
        ) from exc

    campaign.prospect_count = target + 1
    db.flush()


def _is_manual_source(payload: ProspectCreate) -> bool:
    return (payload.source_provider or "").strip().lower() == "manual"


def _reset_sequence_for_manual_restart(existing: Prospect) -> None:
    """
    Carga manual con secuencia: deja el prospecto listo para generar e iniciar de cero
    (si no, «ya iniciada» / toques viejos bloquean el envío inmediato).
    """
    from app.models.enums import ProspectOwnershipStatus

    existing.sequence_playbook_draft = None
    existing.sequence_touch_log = None
    existing.playbook_name = None
    existing.sequence_paused = False
    existing.sequence_fired_milestones = "[]"
    existing.next_touch_at = None
    existing.sequence_started_at = None
    # NOT NULL en DB: no poner None
    existing.sequence_group = "contactado"
    existing.sequence_state = "sin_respuesta"
    existing.linkedin_assisted_draft = None
    # Re-verificar grado en LinkedIn (no heredar Contactar/Mensaje de un ciclo viejo).
    existing.linkedin_connection_status = "none"
    existing.linkedin_last_assisted_at = None
    existing.linkedin_connected_at = None
    existing.linkedin_invite_sent_at = None
    existing.linkedin_assist_status = None
    existing.linkedin_assist_session_id = None
    existing.linkedin_post_connect_draft_at = None
    # Liberar ownership para que quien inserta pueda claim + enviar ya.
    existing.owner_user_id = None
    existing.ownership_status = ProspectOwnershipStatus.libre.value
    existing.claimed_at = None
    existing.ownership_cooldown_until = None
    existing.sequence_completed_at = None


def _adopt_existing_prospect(
    db: Session,
    campaign: Campaign,
    payload: ProspectCreate,
    existing: Prospect,
) -> Prospect:
    """
    Carga manual: si el contacto ya existe, lo reutiliza (y lo mueve a la campaña
    destino si hace falta) en lugar de bloquear con 409.
    """
    moving = int(existing.campaign_id) != int(campaign.id)
    if moving:
        _assert_campaign_prospect_slot(db, campaign)
        existing.campaign_id = campaign.id

    existing.name = payload.name.strip()
    existing.company_name = _resolved_company_name(payload)
    if payload.role:
        existing.role = payload.role.strip()
    if payload.email:
        existing.email = sanitize_stored_email(payload.email.strip())
    if payload.linkedin_url:
        existing.linkedin_url = ingestion.normalize_linkedin_url(payload.linkedin_url)
    if payload.phone is not None:
        existing.phone = sanitize_stored_phone(payload.phone.strip() if payload.phone else None)
    if payload.whatsapp is not None:
        existing.whatsapp = sanitize_stored_phone(
            payload.whatsapp.strip() if payload.whatsapp else None
        ) or existing.phone
    if payload.notes:
        existing.notes = payload.notes.strip()
    if payload.source_provider:
        existing.source_provider = payload.source_provider.strip()

    # «Insertar y empezar secuencia» / secuencia individual: reinicio limpio.
    notes_l = (payload.notes or "").lower()
    wants_sequence = "secuencia individual" in notes_l or "secuencia" in notes_l
    if wants_sequence or moving:
        _reset_sequence_for_manual_restart(existing)

    fields = {
        "country": existing.country or payload.country,
        "industry": existing.industry or payload.industry,
        "role": existing.role,
        "company_name": existing.company_name,
        "email": existing.email,
        "linkedin_url": existing.linkedin_url,
    }
    compat, interest, st, score_reason = _scores_for_campaign(campaign, fields)
    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if _is_manual_source(payload) or is_individual_container_campaign(campaign):
        st = ProspectStatus.imported
    existing.compatibility_score = compat
    existing.interest_probability = interest
    existing.status = st.value
    existing.score_reason = score_reason

    ch, rr = prospect_channels.compute_preferred_channel(existing, campaign)
    existing.preferred_channel = ch
    existing.channel_reason = rr
    db.flush()
    return existing


def _resolved_company_name(payload: ProspectCreate) -> str:
    from app.services.outreach_display_names import resolve_prospect_company_name

    return (
        resolve_prospect_company_name(
            company_name=payload.company_name,
            email=payload.email,
            website=payload.company_website,
        )
        or (payload.company_name or "").strip()
        or "—"
    )


def _persist_new_prospect(db: Session, campaign: Campaign, payload: ProspectCreate) -> Prospect:
    from app.services.crm import exclusions as crm_exclusions

    blocked = crm_exclusions.is_crm_excluded(
        db,
        campaign.company_id,
        email=payload.email,
        company_name=payload.company_name,
        company_website=payload.company_website,
    )
    if blocked is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Este contacto o empresa ya está en la lista de exclusiones "
                f"({blocked.provider}: {blocked.match_type}={blocked.match_value}). "
                "Nexus no lo vuelve a prospectar."
            ),
        )
    dup_company = ingestion.find_duplicate_in_company(
        db,
        company_id=campaign.company_id,
        linkedin_url=payload.linkedin_url,
        email=payload.email,
        phone=payload.phone,
        whatsapp=payload.whatsapp,
    )
    if dup_company is not None:
        if _is_manual_source(payload):
            from app.services.prospect_ownership import is_prospect_locked

            owner_id = getattr(dup_company, "owner_user_id", None)
            if (
                is_prospect_locked(dup_company)
                and owner_id is not None
                and int(owner_id) != int(campaign.seller_id)
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Este contacto ya lo tiene otro vendedor de la empresa. "
                        "Nexus no lo duplica para evitar que dos personas le escriban al mismo."
                    ),
                )
            return _adopt_existing_prospect(db, campaign, payload, dup_company)
        raise HTTPException(
            status_code=409,
            detail=(
                "Este contacto ya existe en la empresa "
                f"(prospecto #{dup_company.id}, campaña id {dup_company.campaign_id}). "
                "No se importan duplicados entre campañas."
            ),
        )
    dup = ingestion.find_duplicate_in_campaign(
        db,
        campaign_id=campaign.id,
        linkedin_url=payload.linkedin_url,
        name=payload.name,
        company_name=payload.company_name,
        email=payload.email,
        source_external_id=payload.source_external_id,
        phone=payload.phone,
        whatsapp=payload.whatsapp,
    )
    if dup is not None:
        if _is_manual_source(payload):
            return _adopt_existing_prospect(db, campaign, payload, dup)
        raise HTTPException(
            status_code=409,
            detail="Ya existe un prospecto duplicado en esta campaña (LinkedIn o nombre + empresa sin LinkedIn).",
        )

    _assert_campaign_prospect_slot(db, campaign)

    company_name = _resolved_company_name(payload)
    fields = {
        "country": payload.country,
        "industry": payload.industry,
        "role": payload.role,
        "company_name": company_name,
        "email": payload.email,
        "linkedin_url": payload.linkedin_url,
    }
    compat, interest, st, score_reason = _scores_for_campaign(campaign, fields)

    # Insert manual / contenedor individual: siempre listo para secuencia (no bloquea por ICP).
    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if _is_manual_source(payload) or is_individual_container_campaign(campaign):
        st = ProspectStatus.imported

    email_val = sanitize_stored_email(payload.email.strip() if payload.email else None)
    phone_val, whatsapp_val, landline_val = _clean_contact_phones(
        payload.phone, payload.whatsapp, getattr(payload, "landline_phone", None)
    )

    row = Prospect(
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        name=payload.name.strip(),
        company_name=company_name,
        role=payload.role.strip() if payload.role else None,
        industry=payload.industry.strip() if payload.industry else None,
        country=payload.country.strip() if payload.country else None,
        linkedin_url=ingestion.normalize_linkedin_url(payload.linkedin_url),
        email=email_val,
        phone=phone_val,
        whatsapp=whatsapp_val,
        landline_phone=landline_val,
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
    user: User = Depends(get_current_user),
    compact: bool = Query(default=False),
) -> list[ProspectRead]:
    from app.services.campaign_visibility import filter_prospects_for_viewer

    rows = db.scalars(
        select(Prospect)
        .where(Prospect.campaign_id == campaign_id)
        .order_by(Prospect.created_at.desc())
    ).all()
    rows = filter_prospects_for_viewer(user, campaign, list(rows))
    return [_serialize(r, campaign, compact=compact) for r in rows]


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


@router.post("/companies/{company_id}/prospects/start-individual")
def start_individual_sequence(
    company_id: int,
    payload: ManualIndividualSequenceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """
    Inserta un prospecto en el contenedor de secuencias individuales.
    No prepara mensajes ni encola LinkedIn hasta «Iniciar secuencia»
    (salvo que el contenedor ya esté running).
    """
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")

    from app.models.product import Product
    from app.services.manual_sequence_kickoff import (
        find_sequence_operator,
        get_or_create_individual_container,
    )

    email = (payload.email or "").strip()
    linkedin = (payload.linkedin_url or "").strip()
    phone = (payload.phone or payload.whatsapp or "").strip()
    if not (
        (email and "@" in email)
        or ("linkedin.com" in linkedin.lower())
        or phone
    ):
        raise HTTPException(
            status_code=400,
            detail="Indicá al menos un canal: email, LinkedIn o teléfono/WhatsApp.",
        )

    product = db.get(Product, int(payload.product_id))
    if product is None or product.company_id != company_id:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    operator = find_sequence_operator(db, company_id=company_id, preferred=user)
    campaign = get_or_create_individual_container(
        db,
        company_id=company_id,
        product=product,
        operator=operator,
    )

    # Plan elegido en el formulario de insert (obligatorio): define toques/canales de ESTA secuencia.
    campaign.sequence_plan = payload.sequence_plan
    fu = (payload.sequence_plan or {}).get("follow_up") or {}
    campaign.post_sequence_followup_enabled = bool(
        payload.post_sequence_followup_enabled
        if payload.post_sequence_followup_enabled is not None
        else fu.get("enabled", True)
    )
    if campaign.post_sequence_followup_enabled:
        campaign.followup_delay_days = payload.followup_delay_days
        if campaign.max_auto_followups is None or int(campaign.max_auto_followups or 0) < 1:
            campaign.max_auto_followups = 1
    else:
        campaign.followup_delay_days = None
        campaign.max_auto_followups = None
    db.flush()

    data = ProspectCreate(
        name=payload.resolved_display_name(),
        company_name=payload.company_name,
        role=payload.role,
        industry=payload.industry,
        country=payload.country,
        linkedin_url=payload.linkedin_url,
        email=payload.email,
        phone=payload.phone,
        whatsapp=payload.whatsapp or payload.phone,
        company_website=payload.company_website,
        source_provider="manual",
        source_external_id=payload.source_external_id,
        notes=(
            (payload.notes or "").strip()
            or "Carga manual — secuencia individual (fuera de campaña)."
        ),
    )
    if "secuencia individual" not in (data.notes or "").lower():
        data = data.model_copy(update={"notes": f"{data.notes} — secuencia individual"})

    row = _persist_new_prospect(db, campaign, data)
    db.flush()

    # Meta del contenedor = stock real (nunca “falta cupo” → nunca dispara búsqueda).
    from app.services.campaign_prospects import count_campaign_prospects

    imported = count_campaign_prospects(db, campaign.id)
    campaign.prospect_count = max(imported, 1)
    db.flush()

    already_running = (campaign.status or "").lower() == CampaignStatus.running.value
    enrich_meta: dict = {
        "enriching": False,
        "status": "none",
        "message": None,
        "max_seconds": 180,
    }

    # Enrich + kickoff (y el crédito) al iniciar. Si el contenedor ya corre, arranca ahora.
    if already_running:
        try:
            from app.services.manual_channel_enrich_job import (
                MANUAL_CHANNEL_ENRICH_MAX_SECONDS,
                begin_manual_channel_enrich,
                schedule_manual_channel_enrich,
            )

            enrich_meta = begin_manual_channel_enrich(
                db,
                row,
                sequence_plan=payload.sequence_plan,
            )
            enrich_meta.setdefault("max_seconds", MANUAL_CHANNEL_ENRICH_MAX_SECONDS)
            db.flush()
            if enrich_meta.get("enriching"):
                db.commit()
                db.refresh(row)
                schedule_manual_channel_enrich(
                    int(row.id),
                    actor_user_id=int(user.id),
                    kickoff_if_running=True,
                )
                camp = db.get(Campaign, campaign.id) or campaign
                notes = [
                    enrich_meta.get("message")
                    or "Buscando información de canales…",
                    "Al arrancar se descuenta 1 crédito. La secuencia sigue al completar la búsqueda.",
                ]
                return {
                    "deferred": False,
                    "kickoff_pending": True,
                    "credit_consumed": 0,
                    "prospect_id": row.id,
                    "campaign_id": campaign.id,
                    "product_id": campaign.product_id,
                    "channel_enrich": enrich_meta,
                    "notes": notes,
                    "message": " ".join(notes),
                    "prospect": _serialize(row, camp),
                }
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).info(
                "individual insert enrich begin skipped prospect=%s: %s", row.id, exc
            )

        db.commit()
        db.refresh(row)
        from app.services.campaign_activation import _schedule_individual_kickoffs_background

        _schedule_individual_kickoffs_background(
            campaign_id=int(campaign.id),
            prospect_ids=[int(row.id)],
            actor_user_id=int(user.id),
        )
        camp = db.get(Campaign, campaign.id) or campaign
        return {
            "deferred": False,
            "kickoff_pending": True,
            "credit_consumed": 0,
            "prospect_id": row.id,
            "campaign_id": campaign.id,
            "product_id": campaign.product_id,
            "channel_enrich": enrich_meta,
            "notes": [
                "Prospecto guardado. Arrancando la secuencia (1 crédito) en segundo plano…",
            ],
            "message": "Prospecto guardado. La secuencia está arrancando.",
            "prospect": _serialize(row, camp),
        }

    db.commit()
    db.refresh(row)
    camp = db.get(Campaign, campaign.id) or campaign
    return {
        "deferred": True,
        "credit_consumed": 0,
        "prospect_id": row.id,
        "campaign_id": campaign.id,
        "product_id": campaign.product_id,
        "channel_enrich": enrich_meta,
        "notes": [
            "Prospecto guardado. Al iniciar la secuencia se busca lo que falte y se descuenta 1 crédito.",
        ],
        "message": "Prospecto guardado. La secuencia no arranca hasta que la inicies.",
        "prospect": _serialize(row, camp),
    }


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
        prospect.email = sanitize_stored_email(
            str(data["email"]).strip() if data["email"] else None
        )
    if "phone" in data or "whatsapp" in data:
        phone_in = data["phone"] if "phone" in data else prospect.phone
        wa_in = data["whatsapp"] if "whatsapp" in data else prospect.whatsapp
        phone_val, wa_val = _clean_contact_phones(
            str(phone_in).strip() if phone_in else None,
            str(wa_in).strip() if wa_in else None,
        )
        if "phone" in data:
            prospect.phone = phone_val
        if "whatsapp" in data:
            prospect.whatsapp = wa_val
        elif "phone" in data and phone_val and not prospect.whatsapp:
            prospect.whatsapp = phone_val
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
        dup_co = ingestion.find_duplicate_in_company(
            db,
            company_id=prospect.company_id,
            linkedin_url=prospect.linkedin_url,
            email=prospect.email,
            phone=prospect.phone,
            whatsapp=prospect.whatsapp,
            exclude_prospect_id=prospect.id,
        )
        if dup_co is not None:
            raise HTTPException(
                status_code=409,
                detail="El email, LinkedIn o teléfono ya está usado por otro prospecto en la empresa.",
            )
        fields = {
            "country": prospect.country,
            "industry": prospect.industry,
            "role": prospect.role,
            "company_name": prospect.company_name,
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


@router.post("/prospects/{prospect_id}/sequence/pause", response_model=ProspectRead)
def pause_prospect_sequence(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
) -> ProspectRead:
    """Pausa la secuencia de ESTE prospecto (los demás de la campaña siguen)."""
    _ = prospect_id
    camp = db.get(Campaign, prospect.campaign_id)
    if camp is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    if prospect.sequence_started_at is None:
        raise HTTPException(status_code=400, detail="Este prospecto todavía no tiene secuencia iniciada.")
    prospect.sequence_paused = True
    multichannel_sequence._append_log(
        camp,
        f"{getattr(user, 'name', None) or user.email}: pausó la secuencia de {prospect.name}.",
        kind="pause",
    )
    db.commit()
    db.refresh(prospect)
    return _serialize(prospect, camp)


@router.post("/prospects/{prospect_id}/sequence/resume", response_model=ProspectRead)
def resume_prospect_sequence(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
) -> ProspectRead:
    """Reanuda la secuencia de ESTE prospecto sin tocar al resto."""
    _ = prospect_id
    camp = db.get(Campaign, prospect.campaign_id)
    if camp is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    if prospect.sequence_started_at is None:
        raise HTTPException(status_code=400, detail="Este prospecto todavía no tiene secuencia iniciada.")
    prospect.sequence_paused = False
    multichannel_sequence._append_log(
        camp,
        f"{getattr(user, 'name', None) or user.email}: reanudó la secuencia de {prospect.name}.",
        kind="resume",
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
