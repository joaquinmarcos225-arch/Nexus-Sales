from enum import Enum


class UserRole(str, Enum):
    sdr = "sdr"
    manager = "manager"
    gerente = "gerente"
    owner = "owner"  # dueño del workspace; mismos poderes de empresa que Director (B2B sales-led)


class ProspectOwnershipStatus(str, Enum):
    """Estado de propiedad del prospecto en la empresa."""

    libre = "libre"
    tomado = "tomado"
    en_secuencia = "en_secuencia"
    secuencia_finalizada = "secuencia_finalizada"
    liberado = "liberado"


class CampaignStatus(str, Enum):
    draft = "draft"
    ready = "ready"
    running = "running"
    paused = "paused"
    completed = "completed"


class AutopilotStatus(str, Enum):
    off = "off"
    running = "running"
    paused = "paused"
    completed = "completed"


class OutreachEmailMode(str, Enum):
    """Envío real de emails (NEXUS_REAL_MODE): borrador en Gmail vs envío automático."""

    draft_only = "draft_only"
    auto_send = "auto_send"


class InboundReplyMode(str, Enum):
    """Respuesta automática a inbound Gmail por campaña."""

    draft_only = "draft_only"
    auto_send = "auto_send"


INBOUND_REPLY_DELAY_CHOICES = (1, 2, 5)


class ProspectStatus(str, Enum):
    imported = "imported"
    compatible = "compatible"
    not_compatible = "not_compatible"
    contacted = "contacted"
    replied = "replied"
    interested = "interested"
    not_interested = "not_interested"
    meeting_booked = "meeting_booked"
    failed = "failed"


class PipelineStage(str, Enum):
    """Etapas comerciales (independientes del status técnico de outreach)."""

    nuevo = "nuevo"
    contactado = "contactado"
    respondio = "respondio"
    interesado = "interesado"
    reunion_agendada = "reunion_agendada"
    propuesta_enviada = "propuesta_enviada"
    negociacion = "negociacion"
    cerrado_ganado = "cerrado_ganado"
    cerrado_perdido = "cerrado_perdido"


class MeetingStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    completed = "completed"
    canceled = "canceled"
    no_show = "no_show"


class IntegrationProvider(str, Enum):
    """Proveedor de cuenta conectada (SDR/AE)."""

    gmail = "gmail"
    google_calendar = "google_calendar"
    whatsapp = "whatsapp"
    linkedin = "linkedin"


class IntegrationStatus(str, Enum):
    """Estado persistido en `ConnectedAccount.status`."""

    not_connected = "not_connected"
    connected = "connected"
    error = "error"
    extension_not_installed = "extension_not_installed"
    extension_connected = "extension_connected"


class MarketScope(str, Enum):
    """Alcance de mercado del producto (qué tipos de campaña admite)."""

    b2b = "b2b"
    b2c = "b2c"
    both = "both"


class OutreachMode(str, Enum):
    """Modo concreto de una campaña (siempre B2B o B2C; nunca híbrido)."""

    b2b = "b2b"
    b2c = "b2c"
