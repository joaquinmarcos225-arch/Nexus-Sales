"""Perfiles unificados y outreach MVP (sin Phantom)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CompanyProfileBlock(BaseModel):
    name: str
    industry: str | None = None
    size: str | None = None
    website: str | None = None
    domain: str | None = None
    icp_score: int | None = None
    enrichment_source: str | None = None
    enrichment_confidence: int | None = None
    corporate_email: str | None = None


class PersonProfileBlock(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    whatsapp_number: str | None = None
    linkedin_url: str | None = None
    confidence: int | None = None
    source: str | None = None


class OutreachChannelDraft(BaseModel):
    channel: Literal["email", "linkedin", "whatsapp"]
    subject: str | None = None
    body: str
    edited: bool = False


class SdrReasoningRead(BaseModel):
    """Razonamiento SDR previo al mensaje (resultado y qué hacemos — no suposición de dolor)."""

    probable_problem: str = Field(
        default="",
        description="Resultado principal que generamos para clientes (no dolor del prospecto).",
    )
    why_it_matters: str = Field(
        default="",
        description="Por qué escribimos ahora / relevancia para el rol del prospecto.",
    )
    hypothesis: str = Field(
        default="",
        description="Qué hacemos / cómo lo hacemos (breve).",
    )
    response_question: str = ""
    selling_to_role: str = Field(
        default="",
        description="Rol explícito al que apunta el mensaje (decisión ICP vs cargo real).",
    )


class RoleAlignmentRead(BaseModel):
    icp_target_role: str = ""
    prospect_actual_role: str = ""
    aligned: bool = False
    alignment_level: Literal["match", "partial", "mismatch", "unknown"] = "unknown"
    match_score: int = Field(default=0, ge=0, le=100)
    warning: str | None = None
    selling_to_role: str = ""
    selling_rationale: str = ""


class IcpScoreBreakdownRead(BaseModel):
    """Desglose auditable del score ICP a nivel contacto."""

    industry_score: int = Field(default=0, ge=0, le=100)
    role_score: int = Field(default=0, ge=0, le=100)
    company_size_score: int = Field(default=0, ge=0, le=100)
    country_score: int = Field(default=0, ge=0, le=100)
    additional_signals_score: int = Field(default=0, ge=0, le=100)
    final_score: int = Field(default=0, ge=0, le=100)
    company_only_score: int | None = Field(
        default=None,
        description="Score ICP solo de la empresa (sin cargo del contacto).",
    )
    legacy_compatibility_score: int | None = Field(
        default=None,
        description="Score anterior del pipeline (puede sobrevalorar sin rol).",
    )
    role_mismatch_cap_applied: bool = False
    notes: list[str] = Field(default_factory=list)
    formula_explanation: str = ""


class OutreachBannedMatchRead(BaseModel):
    field: str = ""
    rule: str = ""
    phrase: str = ""


class OpenAIGenerationDebugRead(BaseModel):
    """Depuración completa de una llamada OpenAI (prompt, respuesta raw, parseo)."""

    channel: str | None = None
    step_day: int | None = None
    model: str = ""
    prompt_system: str = ""
    prompt_user: str = ""
    raw_response: str = ""
    stripped_response: str | None = None
    expected_json_schema: str = ""
    parse_error: str | None = None
    stacktrace: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ValidationBlockCheckRead(BaseModel):
    key: str
    label: str
    ok: bool = False
    value: str = ""
    issue: str | None = None


class OutreachValidationReportRead(BaseModel):
    valid: bool = False
    summary: str = ""
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    word_count: int | None = None
    char_count: int | None = None
    rejected_subject: str | None = None
    rejected_body: str = ""
    rejected_sections: dict[str, str] | None = None
    rejected_internal: dict[str, str] | None = None
    block_checklist: list[ValidationBlockCheckRead] = Field(default_factory=list)
    missing_blocks: list[str] = Field(default_factory=list)
    how_we_do_trace: dict[str, Any] | None = None
    banned_matches: list[OutreachBannedMatchRead] = Field(default_factory=list)
    channel: str | None = None
    step_day: int | None = None
    attempts: int = 0
    generation_debug: OpenAIGenerationDebugRead | None = None


class PlaybookTouchRead(BaseModel):
    """Un toque del playbook (borrador, no enviado)."""

    day: int
    channel: Literal["email", "linkedin", "whatsapp"]
    objective: str
    subject: str | None = None
    body: str = ""
    touch_index: int = Field(default=1, ge=1, description="Número de toque en la secuencia del lead.")
    generated_at: str | None = None
    edited: bool = False
    sdr_reasoning: SdrReasoningRead | None = None


class PlaybookPendingTouchRead(BaseModel):
    """Próximo toque sin generar aún."""

    day: int
    channel: Literal["email", "linkedin", "whatsapp"]
    objective: str
    touch_index: int = 1


class PlaybookStateRead(BaseModel):
    paused: bool = False
    pause_reason: str | None = None
    completed: list[PlaybookTouchRead] = Field(default_factory=list)
    available_channels: list[str] = Field(default_factory=list)
    pending: PlaybookPendingTouchRead | None = None


class OutreachGenerateResultRead(BaseModel):
    ok: bool
    message: str = ""
    detail: str | None = None
    touch: PlaybookTouchRead | None = None
    validation: OutreachValidationReportRead | None = None
    testing: bool = False
    openai_configured: bool = False
    pipeline: "LeadSourcingPipelineRead | None" = None


class OutreachBundleRead(BaseModel):
    email_initial: OutreachChannelDraft | None = None
    followup_1: OutreachChannelDraft | None = None
    followup_2: OutreachChannelDraft | None = None
    linkedin: OutreachChannelDraft | None = None
    whatsapp: OutreachChannelDraft | None = None
    generated_at: str | None = None


class AISDRInsightRead(BaseModel):
    why_selected: str = ""
    icp_fit_reason: str = ""
    reply_probability: int = Field(default=0, ge=0, le=100)
    meeting_probability: int = Field(default=0, ge=0, le=100)
    next_action: str = ""


class LeadProfileRead(BaseModel):
    external_id: str
    company: CompanyProfileBlock
    person: PersonProfileBlock
    prospecting_context: str | None = Field(
        default=None,
        description="Contexto ICP + datos encontrados en prospección para personalizar borradores.",
    )
    role_alignment: RoleAlignmentRead | None = None
    icp_score_breakdown: IcpScoreBreakdownRead | None = None
    outreach: OutreachBundleRead | None = None
    playbook_state: PlaybookStateRead | None = None
    ai_sdr: AISDRInsightRead | None = None
    ready_for_outreach: bool = False
    has_real_contact: bool = False
    has_generic_contact: bool = False
    is_company_outreach: bool = False
    no_contact_message: str | None = None


class MvpDomainResolutionMetricsRead(BaseModel):
    companies_found: int = 0
    domains_resolved: int = 0
    domain_resolution_rate_pct: int = Field(default=0, ge=0, le=100)


class MvpContactMetricsRead(BaseModel):
    companies_found: int = 0
    contacts_found: int = 0
    generic_emails_found: int = 0
    emails_found: int = 0
    contacts_ready_outreach: int = 0


class ProspectingLeadRowRead(BaseModel):
    """Tabla de prospección: persona real con canales de contacto."""

    external_id: str
    person_name: str
    company_name: str
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
    whatsapp_number: str | None = None
    phone_source: str | None = None
    outreach_ready: bool = False
    linkedin_valid: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class CompanyContactRowRead(BaseModel):
    company_external_id: str
    company_name: str
    website: str | None = None
    icp_score: int | None = None
    contact_external_id: str | None = None
    person_name: str | None = None
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    confidence: int | None = None
    source: str | None = None
    status_message: str | None = None


class LeadProfileListRead(BaseModel):
    profiles: list[LeadProfileRead] = Field(default_factory=list)


class OutreachGenerateRequest(BaseModel):
    regenerate: bool = False


class OutreachTestingGenerateRequest(BaseModel):
    channel: Literal["email", "linkedin", "whatsapp"]


class PlaybookPriorContextTouchRead(BaseModel):
    """Toque anterior usado como contexto para generar el siguiente."""

    day: int
    channel: Literal["email", "linkedin", "whatsapp"]
    touch_index: int = 1
    subject: str | None = None
    body: str = ""


class PlaybookPreviewProductRead(BaseModel):
    name: str = ""
    original_description: str = ""
    interpreted_summary: str = ""
    extracted_problems: str = ""
    extracted_benefits: str = ""


class PlaybookPreviewAuditRead(BaseModel):
    """Contexto de auditoría de la secuencia completa."""

    product: PlaybookPreviewProductRead
    icp_industry: str = ""
    icp_target_role: str = ""
    prospect_industry: str = ""
    prospect_actual_role: str = ""
    icp_score: int | None = None
    role_alignment: RoleAlignmentRead | None = None
    icp_score_breakdown: IcpScoreBreakdownRead | None = None
    identified_pain: str = ""
    identified_benefit: str = ""


class PlaybookPreviewTouchRead(BaseModel):
    day: int
    channel: Literal["email", "linkedin", "whatsapp"]
    objective: str
    subject: str | None = None
    body: str = ""
    touch_index: int = 1
    expected_state: str = "sin respuesta"
    prior_context: list[PlaybookPriorContextTouchRead] = Field(default_factory=list)
    sdr_reasoning: SdrReasoningRead | None = None
    generated: bool = True
    skipped: bool = False
    skip_reason: str | None = None
    validation_status: Literal["valid", "warning", "rejected"] = "valid"
    validation: OutreachValidationReportRead | None = None


class PlaybookFullPreviewRead(BaseModel):
    ok: bool
    message: str = ""
    detail: str | None = None
    lead_name: str = ""
    company_name: str = ""
    audit: PlaybookPreviewAuditRead | None = None
    touches: list[PlaybookPreviewTouchRead] = Field(default_factory=list)
    stopped_at_day: int | None = None
    valid_count: int = 0
    rejected_count: int = 0
    warning_count: int = 0
    skipped_count: int = 0
    testing: bool = True
    openai_configured: bool = False


class OutreachEditRequest(BaseModel):
    channel: Literal["email", "linkedin", "whatsapp"]
    slot: Literal["initial", "followup_1", "followup_2"] = "initial"
    subject: str | None = None
    body: str


