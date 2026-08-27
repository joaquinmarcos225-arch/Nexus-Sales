# Importar modelos para registrar metadata y relaciones.
from app.models.automation_job_state import AutomationJobState
from app.models.billing_ops_cycle import BillingOpsCycle
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.company_integration import CompanyIntegration
from app.models.connected_account import ConnectedAccount
from app.models.credit_ledger import CreditLedgerEntry
from app.models.credit_wallet import CreditWallet
from app.models.ai_decision_event import AiDecisionEvent
from app.models.ai_instruction import AIInstruction
from app.models.inbound_auto_reply_receipt import InboundAutoReplyReceipt
from app.models.crm_exclusion import CrmExclusion
from app.models.crm_sync_event import CrmSyncEvent
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
from app.models.password_reset import PasswordResetCode
from app.models.product import Product
from app.models.push_subscription import PushSubscription
from app.models.meeting import Meeting
from app.models.lead_sourcing_pipeline import LeadSourcingPipeline
from app.models.nexus_research_cache import NexusResearchCache
from app.models.nexus_contact_cache import (
    NexusCompanyCache,
    NexusContactCache,
    NexusContactDelivery,
)
from app.models.ops_provider_balance import OpsProviderBalance
from app.models.prospect import Prospect
from app.models.prospect_ownership_event import ProspectOwnershipEvent
from app.models.seller_allocation import SellerCreditAllocation
from app.models.sequence_template import SequenceTemplate
from app.models.support_ticket import SupportMessage, SupportThread
from app.models.team import Team
from app.models.user import User

__all__ = [
    "AutomationJobState",
    "BillingOpsCycle",
    "Campaign",
    "AIInstruction",
    "AiDecisionEvent",
    "CampaignStatus",
    "Company",
    "CompanyIntegration",
    "ConnectedAccount",
    "CrmExclusion",
    "CrmSyncEvent",
    "IntegrationProvider",
    "IntegrationStatus",
    "InboundAutoReplyReceipt",
    "Meeting",
    "MeetingStatus",
    "CreditLedgerEntry",
    "CreditWallet",
    "OutreachMessage",
    "OutreachTask",
    "OutreachSequence",
    "PasswordResetCode",
    "Product",
    "PushSubscription",
    "LeadSourcingPipeline",
    "NexusCompanyCache",
    "NexusContactCache",
    "NexusContactDelivery",
    "NexusResearchCache",
    "OpsProviderBalance",
    "Prospect",
    "ProspectOwnershipEvent",
    "PipelineStage",
    "ProspectStatus",
    "SellerCreditAllocation",
    "SequenceTemplate",
    "SupportMessage",
    "SupportThread",
    "Team",
    "User",
    "UserRole",
]
