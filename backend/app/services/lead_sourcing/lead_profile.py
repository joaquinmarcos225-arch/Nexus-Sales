"""Perfil unificado empresa + persona para MVP outreach."""

from __future__ import annotations

from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.schemas.mvp_outreach import (
    AISDRInsightRead,
    CompanyProfileBlock,
    IcpScoreBreakdownRead,
    LeadProfileRead,
    OutreachBundleRead,
    PersonProfileBlock,
    RoleAlignmentRead,
)
from app.services.lead_sourcing.icp_score_audit import compute_icp_score_breakdown
from app.services.lead_sourcing.nexus_outreach_mvp import build_playbook_state_read
from app.services.lead_sourcing.role_alignment import assess_role_alignment
from app.services.lead_sourcing.contact_identity import (
    is_generic_email_contact,
    is_outreach_ready_generic,
    is_outreach_ready_person,
    is_pipeline_contact,
    is_pipeline_generic_contact,
)
from app.services.lead_sourcing.prospeo_contact_validation import is_directory_host


def _build_prospecting_context(
    lead: LeadCandidateRead,
    company_row: CompanyCandidateRead | None,
    *,
    icp_target_phrase: str | None = None,
    campaign_target_industry: str | None = None,
    campaign_target_role: str | None = None,
    role_alignment: RoleAlignmentRead | None = None,
) -> str:
    """Texto para IA: datos reales del lead y la prospección (sin plantillas)."""
    lines: list[str] = []
    if icp_target_phrase:
        lines.append(f"ICP objetivo de campaña: {icp_target_phrase}")
    if campaign_target_industry:
        lines.append(f"Industria ICP campaña: {campaign_target_industry}")
    if campaign_target_role:
        lines.append(f"Rol ICP campaña (objetivo): {campaign_target_role}")
    if company_row:
        if company_row.icp_relevance_score:
            lines.append(f"Relevancia ICP empresa: {company_row.icp_relevance_score}%")
        if company_row.description:
            lines.append(f"Descripción empresa: {company_row.description[:400]}")
        if company_row.industry:
            lines.append(f"Industria detectada: {company_row.industry}")
        if company_row.country:
            lines.append(f"País empresa: {company_row.country}")
        if company_row.employee_count:
            lines.append(f"Empleados aprox.: {company_row.employee_count}")
    if lead.role:
        lines.append(f"Cargo real del contacto: {lead.role}")
    if role_alignment:
        if role_alignment.selling_to_role:
            lines.append(f"Rol al que debe vender el mensaje: {role_alignment.selling_to_role}")
        if role_alignment.warning:
            lines.append(f"Advertencia rol: {role_alignment.warning}")
    if lead.score_breakdown:
        lines.append(f"Encaje scoring: {lead.score_breakdown}")
    if lead.matched_icp_company and lead.matched_icp_company != lead.company_name:
        lines.append(f"Empresa matcheada ICP: {lead.matched_icp_company}")
    if lead.company_domain:
        lines.append(f"Dominio corporativo: {lead.company_domain}")
    if lead.enrichment_source or lead.provider:
        lines.append(f"Fuente contacto: {lead.enrichment_source or lead.provider}")
    if lead.enrichment_confidence is not None:
        lines.append(f"Confianza enriquecimiento: {lead.enrichment_confidence}%")
    return "\n".join(lines)


def _company_for_lead(
    lead: LeadCandidateRead,
    companies: list[CompanyCandidateRead],
) -> CompanyCandidateRead | None:
    key = (lead.linked_company_key or lead.company_name or "").strip().lower()
    for c in companies:
        if c.result_kind != "company":
            continue
        ck = (c.canonical_key or c.external_id or c.name or "").strip().lower()
        if key and ck == key:
            return c
        if (c.name or "").strip().lower() == (lead.company_name or "").strip().lower():
            return c
    return None


def build_lead_profile(
    lead: LeadCandidateRead,
    companies: list[CompanyCandidateRead],
    *,
    outreach: OutreachBundleRead | None = None,
    ai_sdr: AISDRInsightRead | None = None,
    fit_threshold: int = 70,
    icp_target_phrase: str | None = None,
    campaign_target_industry: str | None = None,
    campaign_target_role: str | None = None,
    campaign_target_country: str | None = None,
    campaign_target_company_size: str | None = None,
) -> LeadProfileRead:
    company_row = _company_for_lead(lead, companies)
    role_alignment = assess_role_alignment(campaign_target_role, lead.role)
    icp_breakdown = compute_icp_score_breakdown(
        campaign_industry=campaign_target_industry,
        campaign_country=campaign_target_country,
        campaign_role=campaign_target_role,
        campaign_company_size=campaign_target_company_size,
        prospect_industry=lead.industry or (company_row.industry if company_row else None),
        prospect_country=lead.country or (company_row.country if company_row else None),
        prospect_role=lead.role,
        company_size=company_row.company_size if company_row else None,
        employee_count=company_row.employee_count if company_row else None,
        email=lead.email,
        linkedin_url=lead.linkedin_url,
        company_domain=lead.company_domain or (company_row.company_domain if company_row else None),
        company_icp_relevance_score=company_row.icp_relevance_score if company_row else None,
        legacy_compatibility_score=lead.compatibility_score,
    )
    prospecting_context = _build_prospecting_context(
        lead,
        company_row,
        icp_target_phrase=icp_target_phrase,
        campaign_target_industry=campaign_target_industry,
        campaign_target_role=campaign_target_role,
        role_alignment=role_alignment,
    )
    icp = icp_breakdown.final_score
    domain = lead.company_domain or (company_row.company_domain if company_row else None)
    if not domain and company_row:
        domain = company_row.company_domain

    company_block = CompanyProfileBlock(
        name=lead.company_name or (company_row.name if company_row else "Empresa"),
        industry=lead.industry or (company_row.industry if company_row else None),
        size=(company_row.company_size if company_row else None),
        website=lead.company_website or (company_row.website_url if company_row else None),
        domain=domain,
        icp_score=icp,
        enrichment_source=(company_row.enrichment_source if company_row else lead.enrichment_source),
        enrichment_confidence=(company_row.enrichment_confidence if company_row else None),
        corporate_email=(company_row.corporate_email if company_row else None),
    )

    generic = is_pipeline_generic_contact(lead)
    real = is_pipeline_contact(lead)

    if generic:
        display_name = lead.company_name or "Equipo comercial"
        person_block = PersonProfileBlock(
            name=display_name,
            role=lead.role or "Email genérico",
            email=lead.email,
            phone=lead.phone or lead.whatsapp,
            linkedin_url=None,
            confidence=lead.enrichment_confidence,
            source="generic_pattern",
        )
        ready = is_outreach_ready_generic(lead, fit_threshold=fit_threshold)
        no_msg = None if ready else "Email genérico sin canal válido"
    else:
        person_block = PersonProfileBlock(
            name=lead.name,
            role=lead.role,
            email=lead.email,
            phone=lead.phone,
            whatsapp_number=lead.whatsapp_number or lead.whatsapp,
            linkedin_url=lead.linkedin_url,
            confidence=lead.enrichment_confidence,
            source=lead.enrichment_source or lead.provider,
        )
        ready = real and is_outreach_ready_person(lead, fit_threshold=fit_threshold)
        no_msg = None if real else "Sin contacto encontrado"
        if real and not ready:
            from app.services.lead_sourcing.prospecting_lead import prospecting_missing_fields

            missing = prospecting_missing_fields(lead)
            no_msg = (
                f"Falta: {', '.join(missing)}"
                if missing
                else "Requiere email corporativo y LinkedIn personal"
            )

    corp_dom = (domain or "").strip().lower()
    if corp_dom and is_directory_host(corp_dom):
        ready = False
        no_msg = "Empresa sin dominio corporativo verificado"

    return LeadProfileRead(
        external_id=lead.external_id,
        company=company_block,
        person=person_block,
        prospecting_context=prospecting_context or None,
        role_alignment=role_alignment,
        icp_score_breakdown=icp_breakdown,
        outreach=outreach if ready else None,
        ai_sdr=ai_sdr if ready else None,
        ready_for_outreach=ready,
        has_real_contact=real,
        has_generic_contact=generic,
        is_company_outreach=generic,
        no_contact_message=no_msg,
    )


def build_profiles(
    people: list[LeadCandidateRead],
    companies: list[CompanyCandidateRead],
    profiles_cache: dict[str, dict],
    *,
    fit_threshold: int = 70,
    icp_target_phrase: str | None = None,
    campaign_target_industry: str | None = None,
    campaign_target_role: str | None = None,
    campaign_target_country: str | None = None,
    campaign_target_company_size: str | None = None,
) -> list[LeadProfileRead]:
    out: list[LeadProfileRead] = []
    for lead in people:
        if not is_pipeline_contact(lead) and not is_pipeline_generic_contact(lead):
            continue
        cached = profiles_cache.get(lead.external_id) if isinstance(profiles_cache, dict) else None
        outreach = None
        ai_sdr = None
        playbook_raw: dict | None = None
        if isinstance(cached, dict):
            try:
                if cached.get("outreach"):
                    outreach = OutreachBundleRead.model_validate(cached["outreach"])
                if cached.get("ai_sdr"):
                    ai_sdr = AISDRInsightRead.model_validate(cached["ai_sdr"])
                if isinstance(cached.get("playbook_state"), dict):
                    playbook_raw = cached["playbook_state"]
            except Exception:
                pass
        profile = build_lead_profile(
            lead,
            companies,
            outreach=outreach,
            ai_sdr=ai_sdr,
            fit_threshold=fit_threshold,
            icp_target_phrase=icp_target_phrase,
            campaign_target_industry=campaign_target_industry,
            campaign_target_role=campaign_target_role,
            campaign_target_country=campaign_target_country,
            campaign_target_company_size=campaign_target_company_size,
        )
        if profile.ready_for_outreach and profile.has_real_contact:
            pb = build_playbook_state_read(profile, playbook_raw)
            out.append(profile.model_copy(update={"playbook_state": pb, "outreach": None}))
    return out
