"""Gates de calidad ICP / ruido en selección de prospectos."""

from app.models.enums import ProspectStatus
from app.services.lead_sourcing.icp_import_gate import (
    company_passes_icp_size,
    geo_hard_score,
    icp_identity_hard_reason,
    icp_import_gate_reason,
    industry_hard_score,
    is_noisy_prospect,
    lead_passes_icp_import_gate,
    role_match_passes,
)
from app.services.lead_sourcing.role_alignment import role_match_score
from app.services.prospect_scoring import score_prospect_against_campaign


class _Camp:
    def __init__(self, **kw):
        self.target_role = kw.get("target_role")
        self.target_industry = kw.get("target_industry")
        self.target_country = kw.get("target_country")
        self.target_company_size = kw.get("target_company_size")


class _Lead:
    def __init__(self, **kw):
        self.role = kw.get("role")
        self.industry = kw.get("industry")
        self.country = kw.get("country")
        self.company_name = kw.get("company_name")
        self.company_domain = kw.get("company_domain")
        self.linkedin_url = kw.get("linkedin_url")
        self.email = kw.get("email")
        self.compatibility_score = kw.get("compatibility_score", 80)
        self.company_size = kw.get("company_size")
        self.employee_count = kw.get("employee_count")


def test_head_of_design_not_match_head_of_sales():
    assert role_match_score("Head of Sales", "Head of Design") < 55
    assert role_match_score("Head of Sales", "Head of Customer Success") < 55
    assert role_match_score("Head of Sales", "Head of Sales") >= 90
    assert role_match_score("Head of Sales", "VP of Sales") >= 55
    assert role_match_score("Head of Sales", "Director Comercial Latam") >= 55
    assert role_match_score("Head of Sales", "Gerente de Ventas") >= 55
    assert role_match_score("Head of Sales", "SEO Specialist") < 55
    assert role_match_score("Head of Sales", "Managing Partner") < 55


def test_noise_recruiters_and_careers():
    assert is_noisy_prospect(role="Technical Recruiter", company_name="Acme")
    assert is_noisy_prospect(role="Head of Sales", company_name="SaaS Talent")
    assert is_noisy_prospect(role="CTO", company_name="ExperienceFlow.ai Careers")
    assert is_noisy_prospect(role="Head of Sales", company_name="Agencia Digital Norte")
    assert is_noisy_prospect(role="VP Sales", company_name="Consultora Andes")
    assert not is_noisy_prospect(role="Head of Sales", company_name="Andes Analytics")


def test_score_caps_when_role_mismatches():
    compat, _, status = score_prospect_against_campaign(
        {
            "role": "Technical Recruiter",
            "industry": "SaaS",
            "country": "LATAM - Brasil",
            "company_name": "SaaS Talent",
            "email": "a@saastalent.com",
            "linkedin_url": "https://linkedin.com/in/x",
        },
        campaign_country="LATAM - Brasil",
        campaign_industry="SaaS",
        campaign_role="Head of Sales",
    )
    assert compat < 70
    assert status == ProspectStatus.not_compatible


def test_score_ok_for_real_head_of_sales():
    compat, _, status = score_prospect_against_campaign(
        {
            "role": "Head of Sales",
            "industry": "SaaS",
            "country": "LATAM - Brasil",
            "company_name": "Andes Analytics",
            "email": "a@andes.com",
            "linkedin_url": "https://linkedin.com/in/x",
        },
        campaign_country="LATAM - Brasil",
        campaign_industry="SaaS",
        campaign_role="Head of Sales",
    )
    assert compat >= 70
    assert status == ProspectStatus.compatible


def test_import_gate_blocks_recruiter_even_with_high_score():
    camp = _Camp(
        target_role="Head of Sales",
        target_industry="SaaS",
        target_country="LATAM - Brasil",
    )
    lead = _Lead(
        role="Technical Recruiter",
        industry="SaaS",
        country="Argentina",
        company_name="SaaS Talent",
        compatibility_score=90,
        email="a@saastalent.com",
        linkedin_url="https://linkedin.com/in/x",
    )
    assert not lead_passes_icp_import_gate(lead, camp, fit_threshold=70)


def test_import_gate_allows_aligned_role():
    camp = _Camp(
        target_role="Head of Sales",
        target_industry="SaaS",
        target_country="LATAM - Brasil",
    )
    lead = _Lead(
        role="Head of Sales LATAM",
        industry="SaaS",
        country="Argentina",
        company_name="Andes Analytics",
        compatibility_score=85,
        email="a@andes.com",
        linkedin_url="https://linkedin.com/in/x",
    )
    assert role_match_passes(camp.target_role, lead.role)
    assert lead_passes_icp_import_gate(lead, camp, fit_threshold=70)


def test_import_gate_role_first_allows_score_below_70():
    """Sin industria: país perfecto no debe exigir score 70 (rol-first ~61–64)."""
    camp = _Camp(
        target_role="Head of Sales",
        target_industry=None,
        target_country="LATAM - Brasil",
    )
    lead = _Lead(
        role="Head of Sales",
        industry=None,
        country="México",
        company_name="Andes Analytics",
        compatibility_score=64,
        email="a@andes.com",
        linkedin_url="https://linkedin.com/in/x",
    )
    assert lead_passes_icp_import_gate(lead, camp, fit_threshold=70)
    reason = icp_import_gate_reason(
        campaign_role=camp.target_role,
        campaign_industry=None,
        campaign_country=camp.target_country,
        campaign_company_size=None,
        prospect_role=lead.role,
        prospect_industry=None,
        prospect_country=lead.country,
        company_name=lead.company_name,
        email=lead.email,
        linkedin_url=lead.linkedin_url,
        compatibility_score=61,
        fit_threshold=70,
    )
    assert reason is None


def test_import_gate_rejects_brazil_when_latam_minus_brasil():
    camp = _Camp(
        target_role="Head of Sales",
        target_industry=None,
        target_country="LATAM - Brasil",
    )
    lead = _Lead(
        role="Head of Sales",
        industry=None,
        country="Brasil",
        company_name="Sao Paulo SaaS",
        compatibility_score=90,
        email="a@sp.com",
        linkedin_url="https://linkedin.com/in/br",
    )
    assert not lead_passes_icp_import_gate(lead, camp, fit_threshold=70)


def test_import_gate_with_industry_still_requires_70_when_perfect():
    camp = _Camp(
        target_role="Head of Sales",
        target_industry="SaaS",
        target_country="LATAM - Brasil",
    )
    lead = _Lead(
        role="Head of Sales",
        industry="SaaS",
        country="México",
        company_name="Andes Analytics",
        compatibility_score=64,
        email="a@andes.com",
        linkedin_url="https://linkedin.com/in/x",
    )
    assert not lead_passes_icp_import_gate(lead, camp, fit_threshold=70)


def test_hard_gate_rejects_wrong_industry():
    reason = icp_identity_hard_reason(
        campaign_industry="SaaS B2B",
        campaign_country=None,
        campaign_company_size=None,
        prospect_industry="Oil & Gas",
        prospect_country=None,
    )
    assert reason is not None
    assert "industria" in reason.lower()


def test_hard_gate_rejects_unknown_industry_when_icp_set():
    # Industria configurada + sin evidencia de industria ni otra dim → rechazo.
    from app.services.lead_sourcing.icp_import_gate import ICP_TIER_REJECT, assess_icp_identity

    tier, score, reason = assess_icp_identity(
        campaign_industry="SaaS",
        campaign_country=None,
        campaign_company_size=None,
        prospect_industry=None,
        prospect_country=None,
    )
    assert tier == ICP_TIER_REJECT
    assert reason is not None
    assert "industria" in reason.lower()
    assert score == 0


def test_hard_gate_allows_unknown_industry_when_geo_matches():
    # Industria desconocida pero país alineado → casi perfecto (cupo con evidencia).
    from app.services.lead_sourcing.icp_import_gate import ICP_TIER_NEAR, assess_icp_identity

    tier, _, reason = assess_icp_identity(
        campaign_industry="SaaS",
        campaign_country="LATAM + Brasil",
        campaign_company_size=None,
        prospect_industry=None,
        prospect_country="México",
    )
    assert tier == ICP_TIER_NEAR
    assert reason is None


def test_hard_gate_rejects_wrong_geo():
    reason = icp_identity_hard_reason(
        campaign_industry=None,
        campaign_country="LATAM - Brasil",
        campaign_company_size=None,
        prospect_industry=None,
        prospect_country="Germany",
    )
    assert reason is not None
    assert "ubicación" in reason.lower() or "país" in reason.lower() or "region" in reason.lower()


def test_hard_gate_rejects_unknown_geo_when_only_geo_icp():
    # Solo región configurada + país ausente → rechazo.
    from app.services.lead_sourcing.icp_import_gate import ICP_TIER_REJECT, assess_icp_identity

    tier, _, reason = assess_icp_identity(
        campaign_industry=None,
        campaign_country="EMEA",
        campaign_company_size=None,
        prospect_industry=None,
        prospect_country=None,
    )
    assert tier == ICP_TIER_REJECT
    assert reason is not None
    assert "ubicación" in reason.lower() or "región" in reason.lower() or "region" in reason.lower()
    score, note = geo_hard_score(None, "EMEA")
    assert score == 0
    assert "desconocido" in note.lower()


def test_hard_gate_allows_unknown_geo_when_industry_matches():
    # País ausente pero industria alineada → casi (Prospeo a menudo omite país).
    from app.services.lead_sourcing.icp_import_gate import ICP_TIER_NEAR, assess_icp_identity

    tier, _, reason = assess_icp_identity(
        campaign_industry="SaaS",
        campaign_country="LATAM + Brasil",
        campaign_company_size=None,
        prospect_industry="SaaS B2B",
        prospect_country=None,
    )
    assert tier == ICP_TIER_NEAR
    assert reason is None


def test_hard_gate_rejects_wrong_size():
    assert not company_passes_icp_size(
        campaign_size="51-100",
        employee_count=5000,
    )
    reason = icp_identity_hard_reason(
        campaign_industry=None,
        campaign_country=None,
        campaign_company_size="51-100 empleados",
        prospect_industry=None,
        prospect_country=None,
        employee_count=5000,
    )
    assert reason is not None
    assert "tamaño" in reason.lower()


def test_hard_gate_allows_unknown_size_when_geo_and_industry_match():
    # Headcount ausente pero industria+geo OK → casi (no matar el cupo).
    from app.services.lead_sourcing.icp_import_gate import ICP_TIER_NEAR, assess_icp_identity

    tier, _, reason = assess_icp_identity(
        campaign_industry="SaaS",
        campaign_country="LATAM + Brasil",
        campaign_company_size="51-100",
        prospect_industry="SaaS",
        prospect_country="Brasil",
        employee_count=None,
        company_size=None,
    )
    assert tier == ICP_TIER_NEAR
    assert reason is None


def test_rank_prefers_perfect_over_near():
    from app.services.lead_sourcing.icp_import_gate import icp_lead_rank_key

    camp = _Camp(
        target_role="Head of Sales",
        target_industry="SaaS",
        target_country="LATAM + Brasil",
        target_company_size="51-100",
    )
    perfect = _Lead(
        role="Head of Sales",
        industry="SaaS",
        country="Brasil",
        employee_count=80,
        compatibility_score=80,
    )
    # Near: industria ausente pero geo+tamaño OK.
    near = _Lead(
        role="Head of Sales",
        industry=None,
        country="Brasil",
        employee_count=80,
        compatibility_score=95,
    )
    assert icp_lead_rank_key(perfect, camp) < icp_lead_rank_key(near, camp)


def test_near_with_industry_requires_higher_score():
    """Casi-perfecto (industria desconocida + geo/tamaño OK): exige score pleno."""
    camp = _Camp(
        target_role="Head of Sales",
        target_industry="SaaS",
        target_country="LATAM + Brasil",
        target_company_size="51-100",
    )
    lead = _Lead(
        role="Head of Sales",
        industry=None,
        country="Brasil",
        company_name="Andes Analytics",
        compatibility_score=58,
        employee_count=80,
        email="a@andes.com",
        linkedin_url="https://linkedin.com/in/x",
    )
    assert not lead_passes_icp_import_gate(lead, camp, fit_threshold=70)
    lead.compatibility_score = 70
    assert lead_passes_icp_import_gate(lead, camp, fit_threshold=70)


def test_hard_gate_allows_matching_size():
    assert company_passes_icp_size(campaign_size="51-100", employee_count=80)
    reason = icp_identity_hard_reason(
        campaign_industry="SaaS",
        campaign_country="LATAM + Brasil",
        campaign_company_size="51-100",
        prospect_industry="SaaS",
        prospect_country="Brasil",
        employee_count=75,
    )
    assert reason is None


def test_industry_hard_score_substring():
    score, _ = industry_hard_score("Enterprise SaaS Software", "SaaS")
    assert score >= 75


def test_import_gate_blocks_high_score_wrong_industry():
    camp = _Camp(
        target_role="Head of Sales",
        target_industry="SaaS",
        target_country="LATAM + Brasil",
        target_company_size="51-100",
    )
    lead = _Lead(
        role="Head of Sales",
        industry="Construction",
        country="Brasil",
        company_name="Builders SA",
        compatibility_score=95,
        employee_count=80,
        email="a@builders.com",
        linkedin_url="https://linkedin.com/in/x",
    )
    reason = icp_import_gate_reason(
        campaign_role=camp.target_role,
        campaign_industry=camp.target_industry,
        campaign_country=camp.target_country,
        campaign_company_size=camp.target_company_size,
        prospect_role=lead.role,
        prospect_industry=lead.industry,
        prospect_country=lead.country,
        company_name=lead.company_name,
        email=lead.email,
        linkedin_url=lead.linkedin_url,
        employee_count=lead.employee_count,
        compatibility_score=lead.compatibility_score,
    )
    assert reason is not None
    assert "industria" in reason.lower()
