"""Generación playbook-driven: un toque por vez según día y canal."""

from __future__ import annotations

import json
import os
import random
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.schemas.mvp_outreach import (
    AISDRInsightRead,
    LeadProfileRead,
    PlaybookFullPreviewRead,
    PlaybookPendingTouchRead,
    PlaybookPreviewAuditRead,
    PlaybookPreviewProductRead,
    PlaybookPreviewTouchRead,
    PlaybookPriorContextTouchRead,
    PlaybookStateRead,
    PlaybookTouchRead,
    RoleAlignmentRead,
    SdrReasoningRead,
)
from app.services.lead_sourcing.icp_score_audit import breakdown_from_profile_and_campaign
from app.services.lead_sourcing.role_alignment import assess_role_alignment
from app.services import openai_service as oai
from app.services.ai_instruction_context import campaign_education_blob
from app.services.lead_sourcing.mvp_outreach_playbook import (
    DEFAULT_MVP_PLAYBOOK,
    PlaybookStepDef,
    lead_available_channels,
    openai_configured,
    resolve_next_playbook_step,
)
from app.services.lead_sourcing.prospeo_contact_validation import is_directory_host


def _campaign_dict(campaign: Campaign) -> dict[str, str]:
    product = campaign.product
    return {
        "name": campaign.name,
        "tone": campaign.tone or "",
        "target_industry": campaign.target_industry or "",
        "target_country": campaign.target_country or "",
        "target_role": campaign.target_role or "",
        "calendar_link": campaign.calendar_link or "",
        "preferred_channel_hint": "email",
        "sender_name": campaign.sender_name or "",
        "brand_name": product.name if product else campaign.name,
    }


def _extract_target_notes_section(notes: str, header: str) -> str:
    if not notes or not header:
        return ""
    pattern = rf"{re.escape(header)}[^\n]*\n([\s\S]*?)(?=\n[A-Za-zÁÉÍÓÚáéíóú][^\n]{{0,80}}\n|$)"
    m = re.search(pattern, notes, re.I)
    return (m.group(1).strip() if m else "")


def _product_dict(campaign: Campaign) -> dict[str, str]:
    p = campaign.product
    if not p:
        return {
            "name": "",
            "description": "",
            "value_proposition": "",
            "pain_points": "",
            "benefits": "",
            "target_notes": "",
            "original_description": "",
            "interpreted_summary": "",
            "extracted_problems": "",
            "extracted_benefits": "",
        }
    notes = p.target_notes or ""
    pain = _extract_target_notes_section(notes, "Problemas que resuelve")
    benefits = _extract_target_notes_section(notes, "Beneficios principales")
    description = (p.description or "").strip()
    value_prop = (p.value_proposition or "").strip()
    return {
        "name": p.name or "",
        "description": description[:4000],
        "value_proposition": value_prop[:2000],
        "pain_points": pain[:2000],
        "benefits": benefits[:2000],
        "target_notes": notes[:3000],
        "original_description": description,
        "interpreted_summary": value_prop,
        "extracted_problems": pain,
        "extracted_benefits": benefits,
    }


def _prospect_dict(profile: LeadProfileRead) -> dict[str, str]:
    ra = profile.role_alignment
    actual_role = profile.person.role or ""
    return {
        "name": profile.person.name,
        "company_name": profile.company.name,
        "role": actual_role,
        "industry": profile.company.industry or "",
        "country": "",
        "email": profile.person.email or "",
        "website": profile.company.website or "",
        "domain": profile.company.domain or "",
        "linkedin_url": profile.person.linkedin_url or "",
        "icp_score": str(profile.company.icp_score or ""),
        "company_size": profile.company.size or "",
        "enrichment_source": profile.company.enrichment_source or profile.person.source or "",
        "prospecting_context": profile.prospecting_context or "",
        "icp_target_role": (ra.icp_target_role if ra else "") or "",
        "prospect_actual_role": (ra.prospect_actual_role if ra else actual_role) or actual_role,
        "selling_to_role": (ra.selling_to_role if ra else actual_role) or actual_role,
        "role_warning": (ra.warning if ra else "") or "",
        "role_alignment_level": (ra.alignment_level if ra else "unknown") or "unknown",
    }


def build_playbook_state_read(
    profile: LeadProfileRead,
    raw_state: dict[str, Any] | None,
) -> PlaybookStateRead:
    available = lead_available_channels(
        email=profile.person.email,
        linkedin_url=profile.person.linkedin_url,
        phone=profile.person.phone,
        whatsapp_number=profile.person.whatsapp_number,
    )
    state = raw_state if isinstance(raw_state, dict) else {}
    completed_raw = state.get("completed") if isinstance(state.get("completed"), list) else []
    completed: list[PlaybookTouchRead] = []
    for item in completed_raw:
        if isinstance(item, dict) and item.get("channel"):
            try:
                completed.append(PlaybookTouchRead.model_validate(item))
            except Exception:
                continue
    next_step = resolve_next_playbook_step(state, available)
    pending = None
    if next_step and not state.get("paused"):
        pending = PlaybookPendingTouchRead(
            day=next_step.day,
            channel=next_step.channel,
            objective=next_step.objective,
            touch_index=len(completed) + 1,
        )
    return PlaybookStateRead(
        paused=bool(state.get("paused")),
        pause_reason=state.get("pause_reason"),
        completed=completed,
        available_channels=sorted(available),
        pending=pending,
    )


def _generate_touch_body(
    db: Session,
    campaign: Campaign,
    profile: LeadProfileRead,
    step: PlaybookStepDef,
    *,
    prior_touches: list[dict[str, Any]],
) -> tuple[str | None, str, SdrReasoningRead]:
    from app.services.lead_sourcing.sdr_playbook_outreach import generate_sdr_playbook_touch

    education = campaign_education_blob(db, campaign)
    prospect = _prospect_dict(profile)
    camp = _campaign_dict(campaign)
    product = _product_dict(campaign)
    return generate_sdr_playbook_touch(
        channel=step.channel,
        prospect=prospect,
        campaign=camp,
        product=product,
        education=education,
        step_day=step.day,
        step_objective=step.objective,
        prior_touches=prior_touches,
        tone=campaign.tone or "",
    )


def generate_next_playbook_touch(
    db: Session,
    campaign: Campaign,
    profile: LeadProfileRead,
    playbook_state: dict[str, Any] | None,
    *,
    regenerate_last: bool = False,
) -> tuple[PlaybookTouchRead, dict[str, Any]]:
    if not openai_configured():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY no configurada. Definila en el backend para generar borradores.",
        )
    available = lead_available_channels(
        email=profile.person.email,
        linkedin_url=profile.person.linkedin_url,
        phone=profile.person.phone,
        whatsapp_number=profile.person.whatsapp_number,
    )
    state: dict[str, Any] = dict(playbook_state) if isinstance(playbook_state, dict) else {}
    completed: list[dict[str, Any]] = [
        t for t in (state.get("completed") or []) if isinstance(t, dict)
    ]

    if regenerate_last and completed:
        last = completed[-1]
        step = PlaybookStepDef(
            day=int(last.get("day") or 1),
            channel=last.get("channel") or "email",  # type: ignore[arg-type]
            objective=str(last.get("objective") or ""),
        )
        prior = completed[:-1]
        touch_index = len(completed)
    else:
        if state.get("paused"):
            raise ValueError(
                str(state.get("pause_reason") or "Secuencia pausada — el prospecto respondió.")
            )
        step = resolve_next_playbook_step(state, available)
        if step is None:
            raise ValueError("No hay más toques del playbook para los canales disponibles de este lead.")
        prior = completed
        touch_index = len(completed) + 1

    subject, body, reasoning = _generate_touch_body(db, campaign, profile, step, prior_touches=prior)
    now = datetime.now(UTC).isoformat()
    touch = PlaybookTouchRead(
        day=step.day,
        channel=step.channel,
        objective=step.objective,
        subject=subject,
        body=body,
        touch_index=touch_index,
        generated_at=now,
        edited=False,
        sdr_reasoning=reasoning,
    )
    touch_dict = touch.model_dump()
    if regenerate_last and completed:
        completed[-1] = touch_dict
    else:
        completed.append(touch_dict)
    state["completed"] = completed
    state["paused"] = bool(state.get("paused"))
    return touch, state


def generate_testing_playbook_draft(
    db: Session,
    campaign: Campaign,
    profile: LeadProfileRead,
    playbook_state: dict[str, Any] | None,
    *,
    channel: str,
) -> PlaybookTouchRead:
    """Borrador de prueba por canal — no modifica playbook_state."""
    from app.services.lead_sourcing.mvp_outreach_playbook import (
        Channel,
        playbook_step_for_channel,
        prior_touches_for_testing_step,
    )

    if channel not in ("email", "linkedin", "whatsapp"):
        raise ValueError(f"Canal inválido: {channel}")
    ch: Channel = channel  # type: ignore[assignment]

    if not openai_configured():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY no configurada. Definila en el backend para generar borradores.",
        )

    step = playbook_step_for_channel(ch)
    if step is None:
        raise ValueError(f"Canal no soportado en el playbook: {channel}")

    state = playbook_state if isinstance(playbook_state, dict) else {}
    completed: list[dict[str, Any]] = [
        t for t in (state.get("completed") or []) if isinstance(t, dict)
    ]
    prior = prior_touches_for_testing_step(completed, step)

    subject, body, reasoning = _generate_touch_body(
        db, campaign, profile, step, prior_touches=prior
    )
    now = datetime.now(UTC).isoformat()
    return PlaybookTouchRead(
        day=step.day,
        channel=step.channel,
        objective=step.objective,
        subject=subject,
        body=body,
        touch_index=len(completed) + 1,
        generated_at=now,
        edited=False,
        sdr_reasoning=reasoning,
    )


def _prior_context_reads(completed: list[dict[str, Any]]) -> list[PlaybookPriorContextTouchRead]:
    out: list[PlaybookPriorContextTouchRead] = []
    for i, t in enumerate(completed, start=1):
        if not isinstance(t, dict):
            continue
        body = (t.get("body") or "").strip()
        if not body:
            continue
        ch = t.get("channel") or "email"
        if ch not in ("email", "linkedin", "whatsapp"):
            continue
        out.append(
            PlaybookPriorContextTouchRead(
                day=int(t.get("day") or 0),
                channel=ch,  # type: ignore[arg-type]
                touch_index=int(t.get("touch_index") or i),
                subject=(t.get("subject") or "").strip() or None,
                body=body,
            )
        )
    return out


def _build_preview_audit(
    campaign: Campaign,
    profile: LeadProfileRead,
    product: dict[str, str],
    *,
    identified_pain: str = "",
    identified_benefit: str = "",
) -> PlaybookPreviewAuditRead:
    pain = identified_pain.strip()
    benefit = identified_benefit.strip()
    if not pain and product.get("extracted_problems"):
        pain = product["extracted_problems"][:800]
    if not benefit and product.get("extracted_benefits"):
        benefit = product["extracted_benefits"][:800]
    role_alignment = profile.role_alignment or assess_role_alignment(
        campaign.target_role,
        profile.person.role,
    )
    icp_breakdown = profile.icp_score_breakdown or breakdown_from_profile_and_campaign(
        campaign,
        profile,
        legacy_compatibility_score=profile.company.icp_score,
    )
    return PlaybookPreviewAuditRead(
        product=PlaybookPreviewProductRead(
            name=product.get("name") or "",
            original_description=product.get("original_description") or product.get("description") or "",
            interpreted_summary=product.get("interpreted_summary") or product.get("value_proposition") or "",
            extracted_problems=product.get("extracted_problems") or product.get("pain_points") or "",
            extracted_benefits=product.get("extracted_benefits") or product.get("benefits") or "",
        ),
        icp_industry=campaign.target_industry or "",
        icp_target_role=role_alignment.icp_target_role or campaign.target_role or "",
        prospect_industry=profile.company.industry or "",
        prospect_actual_role=role_alignment.prospect_actual_role or profile.person.role or "",
        icp_score=icp_breakdown.final_score,
        role_alignment=role_alignment,
        icp_score_breakdown=icp_breakdown,
        identified_pain=pain,
        identified_benefit=benefit,
    )


def generate_full_playbook_preview(
    db: Session,
    campaign: Campaign,
    profile: LeadProfileRead,
) -> PlaybookFullPreviewRead:
    """Genera los 7 toques completos en cadena (simulación sin respuesta) — no modifica estado."""
    from app.services.lead_sourcing.mvp_outreach_playbook import (
        DEFAULT_MVP_PLAYBOOK,
        lead_available_channels,
        openai_configured,
        playbook_steps_for_preview,
        prior_touches_for_testing_step,
    )
    from app.services.lead_sourcing.sdr_playbook_outreach import SdrDraftValidationError, SdrResponseParseError

    lead_name = profile.person.name or ""
    company_name = profile.company.name or ""

    if not openai_configured():
        return PlaybookFullPreviewRead(
            ok=False,
            message="OpenAI no configurado",
            detail="OPENAI_API_KEY no configurada en el backend.",
            lead_name=lead_name,
            company_name=company_name,
            openai_configured=False,
        )

    available = lead_available_channels(
        email=profile.person.email,
        linkedin_url=profile.person.linkedin_url,
        phone=profile.person.phone,
        whatsapp_number=profile.person.whatsapp_number,
    )
    product = _product_dict(campaign)
    steps_plan = playbook_steps_for_preview(available, playbook=DEFAULT_MVP_PLAYBOOK)

    simulated: list[dict[str, Any]] = []
    preview_touches: list[PlaybookPreviewTouchRead] = []
    identified_pain = ""
    identified_benefit = ""
    generated_touch_index = 0
    valid_count = 0
    rejected_count = 0
    warning_count = 0

    for playbook_index, (step, generable, skip_reason) in enumerate(steps_plan, start=1):
        prior_reads = _prior_context_reads(simulated)
        if not generable:
            preview_touches.append(
                PlaybookPreviewTouchRead(
                    day=step.day,
                    channel=step.channel,
                    objective=step.objective,
                    touch_index=playbook_index,
                    expected_state="sin respuesta",
                    prior_context=prior_reads,
                    generated=False,
                    skipped=True,
                    skip_reason=skip_reason,
                )
            )
            continue

        generated_touch_index += 1
        prior = prior_touches_for_testing_step(simulated, step)
        validation_status: str = "valid"
        validation = None
        subject: str | None = None
        body = ""
        reasoning = None

        try:
            subject, body, reasoning = _generate_touch_body(
                db, campaign, profile, step, prior_touches=prior
            )
        except SdrDraftValidationError as exc:
            from app.schemas.mvp_outreach import OutreachValidationReportRead

            report = exc.report
            validation = OutreachValidationReportRead.model_validate(report)
            validation_status = "rejected"
            rejected_count += 1
            subject = str(report.get("rejected_subject") or "").strip() or None
            body = str(report.get("rejected_body") or "").strip()
        except SdrResponseParseError as exc:
            from app.schemas.mvp_outreach import OpenAIGenerationDebugRead, OutreachValidationReportRead

            salvage = (exc.salvage_body or "").strip()
            if salvage:
                validation_status = "warning"
                warning_count += 1
                body = salvage
            else:
                validation_status = "rejected"
                rejected_count += 1
                body = ""
            validation = OutreachValidationReportRead(
                valid=False,
                summary=exc.message,
                issues=[exc.debug.get("parse_error") or exc.message],
                rejected_body=salvage,
                channel=step.channel,
                step_day=step.day,
                generation_debug=OpenAIGenerationDebugRead.model_validate(exc.debug),
            )
        except HTTPException as exc:
            from app.schemas.mvp_outreach import OutreachValidationReportRead

            validation_status = "warning"
            warning_count += 1
            detail = str(exc.detail) if exc.detail else str(exc)
            validation = OutreachValidationReportRead(
                valid=False,
                summary=detail,
                issues=[detail],
                channel=step.channel,
                step_day=step.day,
            )
            body = ""
        except Exception as exc:
            from app.schemas.mvp_outreach import OutreachValidationReportRead

            validation_status = "warning"
            warning_count += 1
            detail = str(exc)
            validation = OutreachValidationReportRead(
                valid=False,
                summary=detail,
                issues=[detail],
                channel=step.channel,
                step_day=step.day,
            )
            body = ""

        if validation_status == "valid":
            valid_count += 1
            if step.day == 1 and reasoning:
                identified_pain = reasoning.probable_problem or reasoning.hypothesis or ""
                identified_benefit = reasoning.why_it_matters or ""

        if body:
            simulated.append(
                {
                    "day": step.day,
                    "channel": step.channel,
                    "objective": step.objective,
                    "subject": subject,
                    "body": body,
                    "touch_index": generated_touch_index,
                    "sdr_reasoning": reasoning.model_dump() if reasoning else None,
                }
            )

        preview_touches.append(
            PlaybookPreviewTouchRead(
                day=step.day,
                channel=step.channel,
                objective=step.objective,
                subject=subject,
                body=body,
                touch_index=playbook_index,
                expected_state="sin respuesta",
                prior_context=prior_reads,
                sdr_reasoning=reasoning,
                generated=True,
                validation_status=validation_status,  # type: ignore[arg-type]
                validation=validation,
            )
        )

    audit = _build_preview_audit(
        campaign,
        profile,
        product,
        identified_pain=identified_pain,
        identified_benefit=identified_benefit,
    )
    skipped_count = sum(1 for t in preview_touches if t.skipped)
    processed = len(preview_touches)
    generated_any = valid_count + rejected_count + warning_count

    if processed == 0:
        msg = "No se procesó ningún paso del playbook."
        ok = False
    elif generated_any == 0:
        msg = "Ningún toque pudo generarse — revisá canales disponibles del lead."
        ok = False
    else:
        parts = [f"{valid_count} válido(s)", f"{rejected_count} rechazado(s)", f"{warning_count} advertencia(s)"]
        if skipped_count:
            parts.append(f"{skipped_count} omitido(s)")
        msg = (
            f"Vista previa completa: {processed} paso(s) — "
            + ", ".join(parts)
            + ". La validación no detiene la secuencia en modo testing."
        )
        ok = True

    return PlaybookFullPreviewRead(
        ok=ok,
        message=msg,
        lead_name=lead_name,
        company_name=company_name,
        audit=audit,
        touches=preview_touches,
        stopped_at_day=None,
        valid_count=valid_count,
        rejected_count=rejected_count,
        warning_count=warning_count,
        skipped_count=skipped_count,
        testing=True,
        openai_configured=True,
    )


def generate_ai_sdr_insight(
    db: Session,
    campaign: Campaign,
    profile: LeadProfileRead,
) -> AISDRInsightRead:
    education = campaign_education_blob(db, campaign)
    system = (
        "Sos analista comercial B2B. Respondé SOLO JSON válido con claves:\n"
        '{"why_selected":"","icp_fit_reason":"","reply_probability":0,"meeting_probability":0,"next_action":""}\n'
        "Probabilidades 0-100 enteros. Textos en español, 1-3 frases cada uno."
    )
    user = (
        f"Campaña: {campaign.name} | ICP industria: {campaign.target_industry} | rol: {campaign.target_role}\n"
        f"Empresa: {profile.company.name} | web: {profile.company.website} | ICP score: {profile.company.icp_score}\n"
        f"Persona: {profile.person.name} | cargo: {profile.person.role} | email: {profile.person.email}\n"
        f"Contexto prospección:\n{(profile.prospecting_context or '')[:1200]}\n"
        f"Contexto producto:\n{education[:1200]}"
    )
    icp = profile.company.icp_score or 50
    conf = profile.person.confidence or 50
    fallback_reply = min(95, max(12, int(icp * 0.4 + conf * 0.35)))
    fallback_meeting = min(85, max(8, int(fallback_reply * 0.45)))
    data: dict = {}
    if openai_configured():
        try:
            raw = oai._chat(system, user, temperature=random.uniform(0.4, 0.6), max_output_tokens=400)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    data = json.loads(raw[start : end + 1])
        except Exception:
            data = {}
    return AISDRInsightRead(
        why_selected=str(
            data.get("why_selected")
            or f"Seleccionado por ICP {icp}% y rol {profile.person.role or campaign.target_role or 'decisor'}."
        )[:500],
        icp_fit_reason=str(
            data.get("icp_fit_reason")
            or f"{profile.company.name} encaja en {campaign.target_industry or 'tu ICP'}."
        )[:500],
        reply_probability=max(0, min(100, int(data.get("reply_probability") or fallback_reply))),
        meeting_probability=max(0, min(100, int(data.get("meeting_probability") or fallback_meeting))),
        next_action=str(
            data.get("next_action") or "Generar próximo toque del playbook y validar borrador."
        )[:300],
    )


def generate_for_eligible_profiles(
    db: Session,
    campaign: Campaign,
    profiles: list[LeadProfileRead],
    *,
    limit: int | None = None,
) -> list[LeadProfileRead]:
    """En enrich no auto-genera toques — el SDR dispara «Generar próximo toque»."""
    del db, campaign, limit
    return profiles
