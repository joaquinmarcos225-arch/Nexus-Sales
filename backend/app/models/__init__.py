# Importar modelos para registrar metadata y relaciones.
from app.models.automation_job_state import AutomationJobState
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.credit_wallet import CreditWallet
from app.models.ai_decision_event import AiDecisionEvent
from app.models.ai_instruction import AIInstruction
from app.models.inbound_auto_reply_receipt import InboundAutoReplyReceipt
from app.models.enums import (
    CampaignStatus,
    IntegrationProvider,
    IntegrationStatus,
    MeetingStatus,
    PipelineStage,
    ProspectStatus,
    UserRole,
)
from app.models.outreach import OutreachMessage, OutreachSequence
from app.models.outreach_task import OutreachTask
from app.models.product import Product
from app.models.meeting import Meeting
from app.models.lead_sourcing_pipeline import LeadSourcingPipeline
from app.models.prospect import Prospect
from app.models.prospect_ownership_event import ProspectOwnershipEvent
from app.models.seller_allocation import SellerCreditAllocation
from app.models.team import Team
from app.models.user import User

__all__ = [
    "AutomationJobState",
    "Campaign",
    "AIInstruction",
    "AiDecisionEvent",
    "CampaignStatus",
    "Company",
    "ConnectedAccount",
    "IntegrationProvider",
    "IntegrationStatus",
    "InboundAutoReplyReceipt",
    "Meeting",
    "MeetingStatus",
    "CreditWallet",
    "OutreachMessage",
    "OutreachTask",
    "OutreachSequence",
    "Product",
    "LeadSourcingPipeline",
    "Prospect",
    "ProspectOwnershipEvent",
    "PipelineStage",
    "ProspectStatus",
    "SellerCreditAllocation",
    "Team",
    "User",
    "UserRole",
]
