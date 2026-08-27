"""
Schemas de prospectos: mismo contrato para POST manual, bulk (UI / integraciones)
y futura extensión Chrome sobre LinkedIn (linkedin_url como clave de deduplicación en servidor).

ProspectBulkCreate.agrega listas grandes en una sola request; ProspectCreate es la fila atómica.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PipelineStage, ProspectStatus


class ProspectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    company_name: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=255)
    linkedin_url: str | None = Field(default=None, max_length=2048)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    whatsapp: str | None = Field(default=None, max_length=64)
    landline_phone: str | None = Field(default=None, max_length=64)
    company_website: str | None = Field(default=None, max_length=2048)
    source_provider: str | None = Field(default=None, max_length=32)
    source_external_id: str | None = Field(default=None, max_length=128)
    notes: str | None = None


class ManualIndividualSequenceCreate(ProspectCreate):
    """Carga manual: alcanza con un canal (email / LinkedIn / WhatsApp). Nombre opcional."""

    name: str = Field(default="", max_length=255)
    company_name: str = Field(default="—", max_length=255)
    product_id: int = Field(ge=1, description="Producto a vender (secuencia fuera de campaña).")
    sequence_plan: dict = Field(
        ...,
        description="Plan de secuencia (plantilla Nexus o custom) obligatorio para el kickoff individual.",
    )
    post_sequence_followup_enabled: bool = Field(
        default=True,
        description="Si True, genera follow-up post-secuencia cuando el prospecto no respondió.",
    )
    followup_delay_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Días hasta el follow-up (vacío/None = default de campaña, tip. 30).",
    )

    @field_validator("sequence_plan")
    @classmethod
    def _validate_sequence_plan(cls, v: dict) -> dict:
        from app.core.sequence_templates import validate_plan

        try:
            return validate_plan(v)
        except ValueError as e:
            raise ValueError(str(e)) from e

    @field_validator("name", mode="before")
    @classmethod
    def _blank_name_ok(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("company_name", mode="before")
    @classmethod
    def _blank_company_ok(cls, v: object) -> str:
        if v is None:
            return "—"
        s = str(v).strip()
        return s or "—"

    def resolved_display_name(self) -> str:
        """Nombre para guardar. Si el usuario no puso, placeholder hasta el enrich."""
        if (self.name or "").strip():
            return self.name.strip()
        return "Contacto"


class ProspectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    company_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=255)
    linkedin_url: str | None = Field(default=None, max_length=2048)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    whatsapp: str | None = Field(default=None, max_length=64)
    landline_phone: str | None = Field(default=None, max_length=64)
    company_website: str | None = Field(default=None, max_length=2048)
    notes: str | None = None
    campaign_id: int | None = Field(default=None, ge=1, description="Reasignar campaña de origen.")
    status: ProspectStatus | None = None
    """Cambiar estado del pipeline."""
    recalculate_scores: bool = Field(
        default=False,
        description="Si True, recalcula compatibilidad / interés y re-clasifica en compatible/not_compatible.",
    )
    pipeline_stage: PipelineStage | None = Field(
        default=None,
        description="Etapa comercial (Kanban). Independiente del status técnico de outreach.",
    )
    meeting_suggestion_pending: bool | None = Field(
        default=None,
        description="Marcar manualmente si quedó una sugerencia de reunión IA pendiente.",
    )


class ProspectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    campaign_id: int
    name: str
    company_name: str
    role: str | None
    industry: str | None
    country: str | None
    linkedin_url: str | None
    linkedin_profile_urn: str | None = None
    email: str | None
    phone: str | None
    whatsapp: str | None = None
    landline_phone: str | None = None
    company_website: str | None = None
    source_provider: str | None = None
    source_external_id: str | None = None
    status: ProspectStatus
    compatibility_score: int
    interest_probability: int
    notes: str | None

    outreach_touch_count: int = 0
    last_outbound_at: datetime | None = None
    last_inbound_at: datetime | None = None
    objection_type: str | None = None
    objection_detected_at: datetime | None = None
    interest_level: str = "low"
    meeting_nudge_sent_at: datetime | None = None
    followup_count: int = 0
    last_followup_at: datetime | None = None
    score_reason: str | None = None
    icp_checklist: list[dict] = Field(default_factory=list)
    next_best_action: str | None = None
    pipeline_stage: str = "nuevo"
    meeting_suggestion_pending: bool = False

    preferred_channel: str | None = None
    channel_reason: str | None = None
    channel_enrich_status: str = "none"
    channel_enrich_deadline_at: datetime | None = None
    channel_enrich_message: str | None = None
    channel_find_summary: str | None = None
    activity_code: str = "idle"
    activity_label: str = "Guardado · esperando inicio"
    activity_tone: str = "muted"
    linkedin_assisted_draft: str | None = None
    linkedin_assist_status: str | None = None
    linkedin_assist_session_id: str | None = None
    linkedin_last_assisted_at: datetime | None = None
    linkedin_sdr_marked_sent_at: datetime | None = None
    linkedin_connection_status: str = "none"
    linkedin_mention_next_touch: bool = False
    whatsapp_assist_status: str | None = None
    whatsapp_assisted_draft: str | None = None

    sequence_started_at: datetime | None = None
    sequence_group: str = "contactado"
    sequence_state: str = "sin_respuesta"
    sequence_fired_milestones: str = "[]"
    sequence_paused: bool = False
    reactivation_sent_at: datetime | None = None
    defer_resume_at: datetime | None = None

    owner_user_id: int | None = None
    owner_name: str | None = None
    ownership_status: str = "libre"
    commercial_state: str = "prospeccion"
    commercial_state_label: str = "Prospección"
    commercial_state_is_testing: bool = False
    claimed_at: datetime | None = None
    sequence_completed_at: datetime | None = None
    ownership_cooldown_until: datetime | None = None
    previous_owner_user_id: int | None = None
    can_act: bool = False
    can_claim: bool = False
    can_release: bool = False
    can_reassign: bool = False
    owner_team_name: str | None = None
    last_sequence_label: str | None = None
    released_at: datetime | None = None
    sequence_current_label: str | None = None
    sequence_current_day: int | None = None
    sequence_current_day_label: str | None = None
    next_touch_at: datetime | None = None
    next_touch_label: str | None = None
    last_touch_at: datetime | None = None
    sequence_start_at: datetime | None = None
    sequence_end_at: datetime | None = None
    estimated_release_at: datetime | None = None
    playbook_name: str | None = None
    has_playbook_draft: bool = False
    is_own_prospect: bool = False
    can_start_outreach: bool = False
    can_generate_sequence: bool = False
    can_view_sequence: bool = False
    can_start_sequence: bool = False
    can_complete_outreach: bool = False
    outreach_ready: bool = False
    outreach_prep_action: str | None = None
    outreach_missing_summary: str | None = None
    generate_sequence_block_reason: str | None = None
    start_sequence_block_reason: str | None = None

    created_at: datetime
    updated_at: datetime


class ProspectReassignRequest(BaseModel):
    to_user_id: int = Field(ge=1)


class ProspectCapabilities(BaseModel):
    can_configure_rules: bool = False


class CommercialSummaryRead(BaseModel):
    total: int = 0
    prospeccion: int = 0
    interesados: int = 0
    reuniones_pendientes: int = 0
    reuniones_agendadas: int = 0
    no_prioridad: int = 0
    derivados: int = 0
    no_interesados: int = 0
    clientes: int = 0


class ProspectsWorkspaceRead(BaseModel):
    viewer_role: str
    prospects: list[ProspectRead]
    capabilities: ProspectCapabilities
    commercial_summary: CommercialSummaryRead = Field(default_factory=CommercialSummaryRead)


class ProspectBulkCreate(BaseModel):
    """Misma forma que alta manual × N — apto para importaciones y la futura extensión."""

    prospects: list[ProspectCreate] = Field(min_length=1, max_length=500)


class ProspectSimulateRequest(BaseModel):
    count: int = Field(default=12, ge=1, le=80)
