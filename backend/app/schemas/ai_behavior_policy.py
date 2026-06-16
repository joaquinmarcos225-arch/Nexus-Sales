from typing import Literal

from pydantic import BaseModel, Field

CalendarLinkMode = Literal["never", "on_request_only", "high_intent_only", "soft_suggestion"]
CommercialAggressiveness = Literal["low", "medium", "high"]
CtaFrequency = Literal["rare", "normal", "frequent"]
ResponseLength = Literal["short", "medium", "detailed"]
TechnicalLevel = Literal["plain", "balanced", "technical"]
ConsultativePriority = Literal["value_first", "balanced", "meeting_first"]
MeetingPush = Literal["minimal", "soft", "assertive"]
FollowUpStyle = Literal["warm", "persistent", "direct"]
Formality = Literal["casual", "neutral", "formal"]
Humor = Literal["professional", "light", "relaxed"]
Personality = Literal["professional", "friendly", "challenger"]
FollowupCadence = Literal["conservative", "standard", "aggressive"]


class AiBehaviorPolicyRead(BaseModel):
    calendar_link: CalendarLinkMode = "on_request_only"
    commercial_aggressiveness: CommercialAggressiveness = "low"
    cta_frequency: CtaFrequency = "rare"
    response_length: ResponseLength = "medium"
    technical_level: TechnicalLevel = "balanced"
    consultative_priority: ConsultativePriority = "value_first"
    meeting_push: MeetingPush = "minimal"
    follow_up_style: FollowUpStyle = "warm"
    formality: Formality = "neutral"
    humor: Humor = "professional"
    personality: Personality = "professional"
    followup_cadence: FollowupCadence = "standard"


class AiBehaviorPolicyUpdate(BaseModel):
    calendar_link: CalendarLinkMode | None = None
    commercial_aggressiveness: CommercialAggressiveness | None = None
    cta_frequency: CtaFrequency | None = None
    response_length: ResponseLength | None = None
    technical_level: TechnicalLevel | None = None
    consultative_priority: ConsultativePriority | None = None
    meeting_push: MeetingPush | None = None
    follow_up_style: FollowUpStyle | None = None
    formality: Formality | None = None
    humor: Humor | None = None
    personality: Personality | None = None
    followup_cadence: FollowupCadence | None = None

    def merge_into(self, base: AiBehaviorPolicyRead) -> AiBehaviorPolicyRead:
        data = base.model_dump()
        for key, val in self.model_dump(exclude_unset=True).items():
            if val is not None:
                data[key] = val
        return AiBehaviorPolicyRead.model_validate(data)


class AiBehaviorPolicyFieldHelp(BaseModel):
    """Metadatos para la UI de Educación IA."""

    key: str
    label: str
    description: str
    options: list[dict[str, str]] = Field(default_factory=list)


POLICY_FIELD_HELP: list[AiBehaviorPolicyFieldHelp] = [
    AiBehaviorPolicyFieldHelp(
        key="commercial_aggressiveness",
        label="Intensidad comercial",
        description="Presión general de venta en el tono del SDR IA.",
        options=[
            {"value": "low", "label": "Baja (consultivo)"},
            {"value": "medium", "label": "Media"},
            {"value": "high", "label": "Alta"},
        ],
    ),
    AiBehaviorPolicyFieldHelp(
        key="cta_frequency",
        label="Frecuencia de CTA",
        description="Con qué frecuencia cerrar con llamada a la acción.",
        options=[
            {"value": "rare", "label": "Rara"},
            {"value": "normal", "label": "Normal"},
            {"value": "frequent", "label": "Frecuente"},
        ],
    ),
    AiBehaviorPolicyFieldHelp(
        key="response_length",
        label="Longitud de respuesta",
        description="Extensión típica de mails inbound.",
        options=[
            {"value": "short", "label": "Corta"},
            {"value": "medium", "label": "Media"},
            {"value": "detailed", "label": "Detallada"},
        ],
    ),
    AiBehaviorPolicyFieldHelp(
        key="technical_level",
        label="Nivel técnico",
        description="Cuánto detalle técnico usar al explicar el producto.",
        options=[
            {"value": "plain", "label": "Lenguaje simple"},
            {"value": "balanced", "label": "Equilibrado"},
            {"value": "technical", "label": "Más técnico"},
        ],
    ),
    AiBehaviorPolicyFieldHelp(
        key="follow_up_style",
        label="Estilo de follow-up",
        description="Tono en seguimientos y postergaciones.",
        options=[
            {"value": "warm", "label": "Cálido"},
            {"value": "persistent", "label": "Persistente"},
            {"value": "direct", "label": "Directo"},
        ],
    ),
    AiBehaviorPolicyFieldHelp(
        key="formality",
        label="Formalidad",
        description="Registro del lenguaje en emails.",
        options=[
            {"value": "casual", "label": "Informal"},
            {"value": "neutral", "label": "Neutro"},
            {"value": "formal", "label": "Formal"},
        ],
    ),
    AiBehaviorPolicyFieldHelp(
        key="humor",
        label="Humor",
        description="Tono ligero permitido en mensajes B2B.",
        options=[
            {"value": "professional", "label": "Profesional"},
            {"value": "light", "label": "Ligero"},
            {"value": "relaxed", "label": "Relajado"},
        ],
    ),
]
