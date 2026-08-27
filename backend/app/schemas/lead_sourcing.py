"""Lead Sourcing Engine — MVP: ICP → Web Search → Prospeo → Nexus Outreach."""

from __future__ import annotations

from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.schemas.mvp_outreach import LeadProfileRead

PipelineStage = Literal[
    "idle",
    "searching_companies",
    "companies_found",
    "preparing_phantom",
    "phantom_ready",
    "extracting_people",
    "leads_detected",
    "enriching_contacts",
    "ready_to_import",
    "error",
]

PipelineStep = Literal[
    "companies",
    "extract_companies",
    "prepare_phantom",
    "people",
    "score",
    "enrich",
    "full",
]


class ProviderStatusRead(BaseModel):
    name: str
    configured: bool
    message: str = ""


class ProspeoHealthRead(BaseModel):
    configured: bool = False
    remaining_credits: int | None = None
    used_credits: int | None = None
    current_plan: str | None = None
    rate_limited: bool = False
    insufficient_credits: bool = False
    search_blocked: bool = False
    error_code: str | None = None
    banner_message: str | None = None
    detail: str | None = None
    rate_limited_until: str | None = None


class LeadSourcingStatusRead(BaseModel):
    """Estado de proveedores del pipeline principal."""

    configured: bool = Field(
        description="True si Web Search + Prospeo están listos (MVP).",
    )
    pipeline: list[str] = Field(
        default_factory=lambda: [
            "ICP",
            "Web Search",
            "Prospeo",
            "Nexus Outreach",
        ],
    )
    message: str = ""
    providers: list[ProviderStatusRead] = Field(default_factory=list)
    mvp_ready: bool = Field(
        default=False,
        description="Web Search + Prospeo listos.",
    )
    prospeo_health: ProspeoHealthRead | None = None


class LeadSourcingFilters(BaseModel):
    person_titles: list[str] | None = None
    person_locations: list[str] | None = None
    organization_locations: list[str] | None = None
    q_keywords: str | None = None


class LeadCandidateRead(BaseModel):
    external_id: str
    provider: str = "prospeo"
    first_name: str | None = None
    last_name: str | None = None
    name: str
    company_name: str
    role: str | None = None
    industry: str | None = None
    country: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    whatsapp_number: str | None = None
    landline_phone: str | None = None
    mobile_phone: str | None = None
    phone_source: str | None = Field(
        default=None,
        description="Origen del teléfono: prospeo_search_mobile | prospeo_enrich_mobile | …",
    )
    image_url: str | None = None
    company_website: str | None = None
    compatibility_score: int | None = None
    fit_tier: Literal["good", "low_fit"] | None = None
    score_breakdown: str | None = None
    score_details: dict | None = None
    matched_icp_company: str | None = None
    company_match_ratio: float | None = None
    discard_reason: str | None = None
    has_email: bool = False
    has_phone: bool = False
    has_linkedin: bool = False
    already_in_campaign: bool = False
    enriched_by_prospeo: bool = False
    enrichment_source: str | None = None
    enrichment_confidence: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Confianza del dato de contacto (Prospeo / Web Search).",
    )
    company_domain: str | None = None
    linked_company_key: str | None = None
    visible_in_panel: bool = True
    contact_kind: Literal["person", "company_placeholder", "generic_email"] = "person"


class DiscardedLeadRead(BaseModel):
    """Lead descartado en parse Phantom o marcado solo para auditoría."""

    name: str | None = None
    company_name: str | None = None
    reason: str
    compatibility_score: int | None = None
    score_breakdown: str | None = None
    sample: dict | None = None


class LeadScoreAuditRead(BaseModel):
    external_id: str
    name: str
    company_name: str | None = None
    compatibility_score: int
    fit_tier: str
    score_breakdown: str | None = None
    score_details: dict | None = None
    visible_in_panel: bool = True
    discard_reason: str | None = None


class CompanyCandidateRead(BaseModel):
    external_id: str
    provider: str = "web_search"
    name: str
    website_url: str | None = None
    industry: str | None = None
    country: str | None = None
    employee_count: int | None = None
    city: str | None = None
    result_kind: Literal["company", "directory_source"] = "company"
    quality_score: int = 0
    icp_relevance_score: int = 0
    normalized_company_name: str | None = None
    source_type: str | None = None
    confidence: int | None = None
    description: str | None = None
    canonical_key: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_directory_url: str | None = None
    extracted_from: str | None = None
    company_domain: str | None = None
    domain_source: str | None = Field(
        default=None,
        description="own_website | prospeo | web_search | unresolved",
    )
    domain_trust: Literal["verified", "doubtful", "unresolved"] | None = Field(
        default=None,
        description="verified = Prospeo OK; doubtful = dominio no coincide con empresa.",
    )
    enrichment_source: str | None = None
    enrichment_confidence: int | None = Field(default=None, ge=0, le=100)
    corporate_email: str | None = None
    company_size: str | None = None


class PipelineStageLogRead(BaseModel):
    step: str
    stage: str
    event: str
    message: str = ""
    at: str
    duration_ms: int | None = None
    result_count: int | None = None


class PipelineRunStateRead(BaseModel):
    running: bool = False
    step: str | None = None
    stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    stale: bool = False


class PhantomQueueItemRead(BaseModel):
    kind: str
    name: str
    url: str | None = None
    platform: str = ""
    icp_relevance_score: int = 0
    external_id: str | None = None


class PhantomQueueRead(BaseModel):
    prepared_at: str | None = None
    icp_target_phrase: str | None = None
    role_hint: str | None = None
    location: str | None = None
    icp_keywords: list[str] = Field(default_factory=list)
    company_count: int = 0
    directory_seed_count: int = 0
    blocked_count: int = 0
    total_items: int = 0
    items: list[PhantomQueueItemRead] = Field(default_factory=list)


class PhantomDebugRead(BaseModel):
    agent_id: str | None = None
    agent_name: str | None = None
    container_id: str | None = None
    container_status: str | None = None
    outcome: str | None = None
    outcome_message: str | None = None
    user_action: str | None = None
    leads_count: int = 0
    rows_parsed: int = 0
    raw_rows_count: int = 0
    valid_rows_count: int = 0
    discarded_rows_count: int = 0
    discarded_rows_sample: list[dict] = Field(default_factory=list)
    first_row_keys: list[str] = Field(default_factory=list)
    first_row_sample: dict | None = None
    parse_note: str | None = None
    session_cookie_in_agent: bool | None = None
    argument_sent: dict | None = None
    input_summary: dict | None = None
    linkedin_query_exact: str | None = Field(
        default=None,
        description="Query booleana enviada a PhantomBuster (títulos OR + industria).",
    )
    lead_score_audit: list[LeadScoreAuditRead] = Field(default_factory=list)
    parse_discards_count: int = 0
    company_searches: list[dict] = Field(default_factory=list)
    company_match_audit: list[dict] = Field(default_factory=list)
    company_search_runs: list[dict] = Field(default_factory=list)
    search_strategy: str | None = None
    phantom_test_mode: bool | None = None
    launch_response: dict | None = None
    launch_payload_sent: dict | None = None
    launch_uses_saved_agent_config: bool | None = None
    auth_debug: dict | None = None
    output_source: str | None = None
    output_endpoint: str | None = None
    launch_id: str | None = None
    leads_list_id: str | None = None
    manual_result_url: str | None = None
    has_result_object: bool | None = None
    s3_folders: dict | None = None
    s3_urls_tried: list[str] = Field(default_factory=list)
    result_urls_tried: list[str] = Field(default_factory=list)
    output_attempts: list[dict] = Field(default_factory=list)
    output_keys: list[str] = Field(default_factory=list)
    output_preview: str | None = None
    container_exit_message: str | None = None
    container_poll_timeout: bool | None = None
    poll_iterations: int | None = None
    poll_elapsed_sec: float | None = None
    poll_break: str | None = None
    poll_trace: list[dict] = Field(default_factory=list)
    step_completion: str | None = None
    agent_last_end_message: str | None = None


class EnrichProgressRead(BaseModel):
    """Progreso de enrich por lotes (máx. 3 empresas por click)."""

    processed: int = 0
    total: int = 0
    has_more: bool = False
    batch_size: int = 3
    last_batch_count: int = 0


class LeadSourcingPipelineRead(BaseModel):
    campaign_id: int
    stage: str = "idle"
    stage_label: str = "En espera"
    fit_threshold: int = 70
    display_min_score: int = Field(
        default=30,
        description="Umbral de etiqueta Bajo fit (LEAD_SOURCING_MIN_DISPLAY_SCORE).",
    )
    companies_count: int = 0
    people_count: int = 0
    ready_count: int = 0
    companies: list[CompanyCandidateRead] = Field(default_factory=list)
    people: list[LeadCandidateRead] = Field(default_factory=list)
    discarded_leads: list[DiscardedLeadRead] = Field(default_factory=list)
    lead_score_audit: list[LeadScoreAuditRead] = Field(default_factory=list)
    search_query: str | None = None
    icp_target_phrase: str | None = None
    google_query: str | None = None  # legacy alias (respuesta)
    last_error: str | None = None
    pipeline_steps: list[str] = Field(default_factory=list)
    extraction_stats: dict | None = None
    extracted_companies_count: int = 0
    phantom_queue: PhantomQueueRead | None = None
    phantom_prepared: bool = False
    blocked_sources_count: int = 0
    phantom_debug: PhantomDebugRead | None = None
    stage_logs: list[PipelineStageLogRead] = Field(default_factory=list)
    run_state: PipelineRunStateRead | None = None
    lead_profiles: list["LeadProfileRead"] = Field(default_factory=list)
    mvp_contact_metrics: "MvpContactMetricsRead | None" = None
    domain_resolution_metrics: "MvpDomainResolutionMetricsRead | None" = None
    company_contacts: list["CompanyContactRowRead"] = Field(default_factory=list)
    prospeo_contact_debug: list[dict] = Field(
        default_factory=list,
        description="Descartes Prospeo: empresa objetivo, detectada, dominio email, motivo.",
    )
    domain_resolution_debug: list[dict] = Field(
        default_factory=list,
        description="Resolución dominio corporativo por empresa.",
    )
    prospeo_search_debug: list[dict] = Field(
        default_factory=list,
        description="Diagnóstico search-person Prospeo por empresa.",
    )
    prospeo_health: ProspeoHealthRead | None = None
    enrich_progress: EnrichProgressRead | None = None
    prospecting_leads: list["ProspectingLeadRowRead"] = Field(default_factory=list)
    prospeo_phone_info: dict | None = Field(
        default=None,
        description="Capacidades teléfono/WhatsApp según API Prospeo y modo batch.",
    )


class PipelineRunRequest(BaseModel):
    step: PipelineStep = "full"
    company_limit: int = Field(default=15, ge=1, le=30)
    people_limit: int = Field(default=40, ge=1, le=100)
    fit_threshold: int | None = Field(default=None, ge=50, le=100)


class PipelineRunRead(BaseModel):
    ok: bool
    step: str
    message: str = ""
    pipeline: LeadSourcingPipelineRead | None = None


class LeadSourcingImportRequest(BaseModel):
    external_ids: list[str] = Field(min_length=1, max_length=100)


class LeadSourcingImportRead(BaseModel):
    imported: int = 0
    skipped_duplicates: int = 0
    skipped_missing: int = 0
    errors: list[str] = Field(default_factory=list)
    prospect_ids: list[int] = Field(default_factory=list)


from app.schemas.mvp_outreach import (  # noqa: E402
    CompanyContactRowRead,
    LeadProfileRead,
    MvpContactMetricsRead,
    MvpDomainResolutionMetricsRead,
    OutreachGenerateResultRead,
    ProspectingLeadRowRead,
)

LeadSourcingPipelineRead.model_rebuild()
OutreachGenerateResultRead.model_rebuild()
