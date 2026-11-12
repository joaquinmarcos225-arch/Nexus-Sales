"""Filtros de ruido y gates ICP estrictos (sector, geo, tamaño + rol)."""

from __future__ import annotations

import re
from typing import Any

from app.services import campaign_icp as icp
from app.services.lead_sourcing.role_alignment import campaign_role_matches

# Umbral mínimo de match de rol (0–100) cuando la campaña tiene target_role.
MIN_ROLE_MATCH_FOR_IMPORT = 55
# Score ICP mínimo agregado.
DEFAULT_ICP_FIT_THRESHOLD = 70
# Dimensiones de identidad: umbrales duros (0–100).
MIN_INDUSTRY_HARD = 75
MIN_GEO_HARD = 85
MIN_SIZE_HARD = 80

_ROLE_NOISE = re.compile(
    r"\b("
    r"recruiter|recruiting|recruitment|talent\s*acquisition|sourcer|staffing|"
    r"headhunter|hunter\s*de\s*talentos|"
    r"hr\s*business|people\s*partner|human\s*resources|"
    r"squader|intern\b|estudiante|student\b|"
    r"process\s*master"
    r")\b",
    re.I,
)

_JOB_SEEKER = re.compile(
    r"\b("
    r"open\s*to\s*work|#opentowork|"
    r"buscando\s+(empleo|trabajo|laburo|oportunidad)|"
    r"en\s+b[uú]squeda\s+de\s+(empleo|trabajo|laburo)|"
    r"looking\s+for\s+(a\s+)?(job|work|employment)|"
    r"job\s*seeker|"
    r"disponible\s+para\s+trabajar|"
    r"aspirante|postulante|"
    r"unemployed|sin\s+empleo|"
    r"en\s+transici[oó]n\s+laboral|"
    r"seeking\s+(a\s+)?(job|employment)"
    r")\b",
    re.I,
)

_COMPANY_NOISE = re.compile(
    r"\b("
    r"recruit|recruiting|recruitment|staffing|headhunter|"
    r"talent\s*(agency|solutions|group|partners)?|"
    r"careers?\b|jobs?\s*board|job\s*board|"
    r"consulting\s*firm|digital\s*agency|marketing\s*agency|"
    r"agencia\s+digital|consultora\b|consultoría\b|"
    r"freelance\s+(agency|collective)|creative\s+agency|"
    r"university|college|bootcamp"
    r")\b|"
    r"\b(saas\s+talent)\b|"
    r"careers?\.",
    re.I,
)

_LINKEDIN_NOISE = re.compile(r"/company/|/school/|/showcase/", re.I)

_FIRMO_STATUS_SCORE = {
    "exact": 100,
    "equivalent": 90,
    "similar": 70,
    "review": 50,
    "incomplete": 40,
    "contradictory": 0,
}


def is_noisy_role(role: str | None) -> bool:
    blob = (role or "").strip()
    if not blob:
        return False
    return bool(_ROLE_NOISE.search(blob) or _JOB_SEEKER.search(blob))


def is_noisy_company(company_name: str | None, *, company_domain: str | None = None) -> bool:
    blob = f"{company_name or ''} {company_domain or ''}".strip()
    if not blob:
        return False
    return bool(_COMPANY_NOISE.search(blob))


def is_noisy_prospect(
    *,
    role: str | None = None,
    company_name: str | None = None,
    company_domain: str | None = None,
    linkedin_url: str | None = None,
) -> bool:
    if is_noisy_role(role):
        return True
    if is_noisy_company(company_name, company_domain=company_domain):
        return True
    li = (linkedin_url or "").strip()
    if li and _LINKEDIN_NOISE.search(li):
        return True
    if re.search(r"\bcareers?\b", (company_name or ""), re.I):
        return True
    return False


def role_match_passes(campaign_role: str | None, prospect_role: str | None) -> bool:
    if campaign_role is None or icp.is_icp_token_empty(campaign_role):
        return True
    return campaign_role_matches(campaign_role, prospect_role)


def role_tier_passes(campaign_role: str | None, prospect_role: str | None) -> bool:
    """True si el cargo es exacto, equivalente o similar (no Pre-Sales / subdirector / asistente)."""
    if campaign_role is None or icp.is_icp_token_empty(campaign_role):
        return True
    from app.services.lead_sourcing.role_alignment import classify_role_tier

    return classify_role_tier(campaign_role, prospect_role) is not None


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def industry_hard_score(
    prospect_industry: str | None,
    campaign_industry: str | None,
    *,
    origin: str | None = None,
) -> tuple[int, str]:
    """Score de industria vía el clasificador central. Desconocido = 0."""
    from app.services.lead_sourcing.firmographics import assess_company_firmographics

    firm = assess_company_firmographics(
        campaign_industry=campaign_industry,
        industry=prospect_industry,
        industry_origin=origin,
    )
    st = firm.industry.status
    if st is None:
        return 100, firm.industry.reason
    return _FIRMO_STATUS_SCORE.get(st, 0), firm.industry.reason


def geo_hard_score(prospect_country: str | None, campaign_country: str | None) -> tuple[int, str]:
    """Score duro de región. Desconocido = 0 (no da puntos)."""
    if campaign_country is None or icp.is_icp_token_empty(campaign_country):
        return 100, "ICP región no configurada"
    from app.services.lead_sourcing.icp_region import score_region_alignment

    pc = (prospect_country or "").strip()
    if not pc:
        return 0, "país desconocido"
    score, note = score_region_alignment(campaign_country, prospect_country)
    # El scorer soft da 40 a desconocido; aquí ya lo filtramos arriba.
    if score < 50:
        return 0, note or "país fuera de región ICP"
    return score, note


def parse_employee_bounds(
    company_size: str | None = None,
    employee_count: int | None = None,
) -> tuple[int, int] | None:
    """Misma interpretación de rangos que ``parse_size_bounds`` (``1000+`` es intervalo)."""
    from app.services.lead_sourcing.firmographics import parse_size_bounds

    return parse_size_bounds(company_size=company_size, employee_count=employee_count)


def icp_size_bounds(campaign_size: str | None) -> tuple[int, int] | None:
    if campaign_size is None or icp.is_icp_token_empty(campaign_size):
        return None
    return parse_employee_bounds(company_size=campaign_size)


def size_ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def size_hard_score(
    *,
    campaign_size: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
    origin: str | None = None,
) -> tuple[int, str]:
    """Score de tamaño vía el clasificador central (mismos rangos que ``parse_size_bounds``)."""
    from app.services.lead_sourcing.firmographics import assess_company_firmographics

    firm = assess_company_firmographics(
        campaign_company_size=campaign_size,
        company_size=company_size,
        employee_count=employee_count,
        size_origin=origin,
    )
    st = firm.size.status
    if st is None:
        return 100, firm.size.reason
    return _FIRMO_STATUS_SCORE.get(st, 0), firm.size.reason


def company_passes_icp_size(
    *,
    campaign_size: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
    origin: str | None = None,
) -> bool:
    from app.services.lead_sourcing.firmographics import assess_company_firmographics

    firm = assess_company_firmographics(
        campaign_company_size=campaign_size,
        company_size=company_size,
        employee_count=employee_count,
        size_origin=origin,
    )
    return firm.size.status != "contradictory"


# Tiers para llenar cupo: primero perfectos, después casi perfectos.
ICP_TIER_PERFECT = 0
ICP_TIER_NEAR = 1
ICP_TIER_REJECT = 9


def assess_icp_identity(
    *,
    campaign_industry: str | None,
    campaign_country: str | None,
    campaign_company_size: str | None,
    prospect_industry: str | None,
    prospect_country: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
    industry_origin: str | None = None,
    size_origin: str | None = None,
) -> tuple[int, int, str | None]:
    """
    Evalúa identidad ICP.

    Industria y tamaño vienen del clasificador central. Geo y cargo no se mezclan
    con esas dimensiones: una no compensa a la otra.

    Returns:
      (tier, identity_score_0_100, reject_reason|None)
      tier: ICP_TIER_PERFECT | ICP_TIER_NEAR | ICP_TIER_REJECT

    - Perfecto: dims firmográficas solicitadas confirmed (exact/equivalent) y geo OK.
    - Casi: similar / incomplete / review (cupo, no confirmar).
    - Reject: contradictory; o única dim firmográfica pedida sin dato verificable.
      Tamaño/país ausentes NO rechazan si hay otra dim alineada (Prospeo suele omitirlos).
    """
    from app.services.lead_sourcing.firmographics import assess_company_firmographics

    firm = assess_company_firmographics(
        campaign_industry=campaign_industry,
        campaign_company_size=campaign_company_size,
        industry=prospect_industry,
        industry_origin=industry_origin,
        company_size=company_size,
        employee_count=employee_count,
        size_origin=size_origin,
    )
    geo_score, geo_note = geo_hard_score(prospect_country, campaign_country)

    industry_active = campaign_industry is not None and not icp.is_icp_token_empty(campaign_industry)
    geo_active = campaign_country is not None and not icp.is_icp_token_empty(campaign_country)
    size_active = campaign_company_size is not None and not icp.is_icp_token_empty(
        campaign_company_size
    )

    industry_unknown = firm.industry.status == "incomplete"
    geo_unknown = geo_active and not (prospect_country or "").strip()
    size_unknown = firm.size.status == "incomplete"

    if firm.industry.status == "contradictory":
        return ICP_TIER_REJECT, 0, f"industria no coincide con ICP ({firm.industry.reason})"
    if geo_active and not geo_unknown and geo_score < MIN_GEO_HARD:
        return ICP_TIER_REJECT, 0, f"ubicación no coincide con ICP ({geo_note})"
    if firm.size.status == "contradictory":
        return ICP_TIER_REJECT, 0, f"tamaño no coincide con ICP ({firm.size.reason})"

    # Industria pedida y ausente: rechazar. Sin industria verificable se cuela
    # avícola/legal/eventos con solo rol+país y arruina la calidad del cupo.
    if industry_active and industry_unknown:
        return (
            ICP_TIER_REJECT,
            0,
            "industria desconocida (ICP industria configurada)",
        )

    # Región + industria configuradas: exigir país verificable solo si la industria ya está confirmada.
    # Si ambas faltan (role-first sin país/industria en el hit), → NEAR vía cupo, no rechazo duro.
    if geo_unknown and industry_active and geo_active and not industry_unknown:
        return (
            ICP_TIER_REJECT,
            0,
            "ubicación desconocida (ICP región e industria configuradas)",
        )

    # Región: si es la única dim firmográfica y falta el país → rechazo.
    # Si hay industria/tamaño verificable, desconocido → casi (Prospeo a menudo no manda país).
    if geo_unknown and not industry_active and not size_active:
        return (
            ICP_TIER_REJECT,
            0,
            "ubicación desconocida (ICP región configurada)",
        )

    # Tamaño desconocido: no hard-reject si hay otra evidencia (rol+geo/industria).
    # Prospeo suele omitir headcount; rechazar a ciegas deja campañas en 0.
    # Solo rechazo duro si tamaño es la única dim y falta el dato.
    if size_unknown and not industry_active and not geo_active:
        return (
            ICP_TIER_REJECT,
            0,
            "tamaño desconocido (ICP tamaño configurado)",
        )

    active_scores: list[int] = []
    if firm.industry.status and firm.industry.status != "incomplete":
        active_scores.append(_FIRMO_STATUS_SCORE.get(firm.industry.status, 0))
    if geo_active and not geo_unknown:
        active_scores.append(geo_score)
    if firm.size.status and firm.size.status != "incomplete":
        active_scores.append(_FIRMO_STATUS_SCORE.get(firm.size.status, 0))

    if not active_scores:
        # Dims configuradas pero sin dato usable en ninguna → casi / cupo vía rol.
        # (Los casos “única dim y desconocida” ya se rechazaron arriba.)
        return ICP_TIER_NEAR, 40, None

    identity = int(round(sum(active_scores) / len(active_scores)))

    geo_ok = (not geo_active) or (not geo_unknown and geo_score >= MIN_GEO_HARD)

    def _dim_confirmed(status: str | None) -> bool:
        return status in {"exact", "equivalent"}

    industry_confirmed = (not industry_active) or _dim_confirmed(firm.industry.status)
    size_confirmed = (not size_active) or _dim_confirmed(firm.size.status)
    firm_ok = firm.combined is None or firm.combined == "confirmed"

    perfect = bool(
        firm_ok
        and geo_ok
        and not industry_unknown
        and not geo_unknown
        and not size_unknown
        and industry_confirmed
        and size_confirmed
    )

    if perfect:
        return ICP_TIER_PERFECT, identity, None
    return ICP_TIER_NEAR, identity, None


def icp_identity_hard_reason(
    *,
    campaign_industry: str | None,
    campaign_country: str | None,
    campaign_company_size: str | None,
    prospect_industry: str | None,
    prospect_country: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
    industry_origin: str | None = None,
    size_origin: str | None = None,
    perfect_only: bool = False,
) -> str | None:
    """
    None = OK (perfecto, o casi si perfect_only=False).
    str = rechazo (mismatch conocido o, si perfect_only, no es perfecto).
    """
    tier, _score, reason = assess_icp_identity(
        campaign_industry=campaign_industry,
        campaign_country=campaign_country,
        campaign_company_size=campaign_company_size,
        prospect_industry=prospect_industry,
        prospect_country=prospect_country,
        company_size=company_size,
        employee_count=employee_count,
        industry_origin=industry_origin,
        size_origin=size_origin,
    )
    if tier == ICP_TIER_REJECT:
        return reason or "identidad ICP rechazada"
    if perfect_only and tier != ICP_TIER_PERFECT:
        return reason or "no es match ICP perfecto"
    return None


def contact_quality_rank(lead: Any) -> int:
    """0 = mail y celular verificados · 1 = uno de los dos · 2 = ninguno."""
    verified = int(bool(getattr(lead, "email_verified", False))) + int(
        bool(getattr(lead, "mobile_verified", False))
    )
    return 2 - verified


def icp_lead_rank_key(lead: Any, campaign: Any) -> tuple[int, int, int, int]:
    """
    Clave de ordenamiento para llenar cupo (solo ordena, no descarta a nadie):
    1) grupo de contacto: mail+celular verificados → uno solo → ninguno (último recurso)
    2) dentro del grupo, mejor ICP: tier (perfecto → casi), identity y compatibility_score
    """
    emp = getattr(lead, "employee_count", None)
    try:
        emp_i = int(emp) if emp is not None else None
    except (TypeError, ValueError):
        emp_i = None
    tier, identity, _ = assess_icp_identity(
        campaign_industry=getattr(campaign, "target_industry", None),
        campaign_country=getattr(campaign, "target_country", None),
        campaign_company_size=getattr(campaign, "target_company_size", None),
        prospect_industry=getattr(lead, "industry", None),
        prospect_country=getattr(lead, "country", None),
        company_size=getattr(lead, "company_size", None),
        employee_count=emp_i,
        industry_origin=getattr(lead, "industry_origin", None),
        size_origin=getattr(lead, "size_origin", None),
    )
    compat = int(getattr(lead, "compatibility_score", None) or 0)
    return (contact_quality_rank(lead), tier, -identity, -compat)


def icp_import_gate_reason(
    *,
    campaign_role: str | None,
    campaign_industry: str | None,
    campaign_country: str | None,
    campaign_company_size: str | None,
    prospect_role: str | None,
    prospect_industry: str | None,
    prospect_country: str | None,
    company_name: str | None = None,
    company_domain: str | None = None,
    linkedin_url: str | None = None,
    email: str | None = None,
    company_size: str | None = None,
    employee_count: int | None = None,
    industry_origin: str | None = None,
    size_origin: str | None = None,
    compatibility_score: int | None = None,
    fit_threshold: int = DEFAULT_ICP_FIT_THRESHOLD,
    perfect_only: bool = False,
) -> str | None:
    """
    None = OK para importar. str = motivo de rechazo.
    Por defecto admite perfecto + casi perfecto (para cumplir cantidad).
    perfect_only=True exige match idéntico en dims ICP.
    """
    if is_noisy_prospect(
        role=prospect_role,
        company_name=company_name,
        company_domain=company_domain,
        linkedin_url=linkedin_url,
    ):
        return "fuente/rol ruidoso (recruiter, careers, staffing, buscando trabajo, etc.)"

    # Pedís industria X → nada que grite sector Y (aunque Prospeo etiquete mal).
    if campaign_industry is not None and not icp.is_icp_token_empty(campaign_industry):
        from app.services.lead_sourcing.firmographics import sector_mismatch_reason

        off = sector_mismatch_reason(
            campaign_industry,
            company_name=company_name,
            role=prospect_role,
            industry=prospect_industry,
        )
        if off:
            return off

    if not role_tier_passes(campaign_role, prospect_role):
        return "el cargo no coincide con los cargos de la campaña"

    hard = icp_identity_hard_reason(
        campaign_industry=campaign_industry,
        campaign_country=campaign_country,
        campaign_company_size=campaign_company_size,
        prospect_industry=prospect_industry,
        prospect_country=prospect_country,
        company_size=company_size,
        employee_count=employee_count,
        industry_origin=industry_origin,
        size_origin=size_origin,
        perfect_only=perfect_only,
    )
    if hard:
        return hard

    score = compatibility_score
    if score is None:
        from app.services.prospect_scoring import score_prospect_against_campaign

        score, _, _ = score_prospect_against_campaign(
            {
                "role": prospect_role,
                "industry": prospect_industry,
                "industry_origin": industry_origin,
                "size_origin": size_origin,
                "company_size": company_size,
                "employee_count": employee_count,
                "country": prospect_country,
                "email": email,
                "linkedin_url": linkedin_url,
            },
            campaign_country=campaign_country,
            campaign_industry=campaign_industry,
            campaign_role=campaign_role,
            campaign_company_size=campaign_company_size,
        )

    has_icp = any(
        not icp.is_icp_token_empty(v)
        for v in (campaign_role, campaign_industry, campaign_country, campaign_company_size)
        if v is not None
    )
    # Casi-perfectos: umbral un poco más bajo para poder completar cupo.
    from app.services.lead_sourcing.firmographics import assess_company_firmographics

    tier, _, _ = assess_icp_identity(
        campaign_industry=campaign_industry,
        campaign_country=campaign_country,
        campaign_company_size=campaign_company_size,
        prospect_industry=prospect_industry,
        prospect_country=prospect_country,
        company_size=company_size,
        employee_count=employee_count,
        industry_origin=industry_origin,
        size_origin=size_origin,
    )
    industry_active = campaign_industry is not None and not icp.is_icp_token_empty(
        campaign_industry
    )
    firm = assess_company_firmographics(
        campaign_industry=campaign_industry,
        industry=prospect_industry,
        industry_origin=industry_origin,
    )
    industry_unknown = firm.industry.status == "incomplete"
    # Sin industria (rol-first): no exigir score "perfecto" 70 — el cupo no se llena.
    # Con industria + match perfecto: fit_threshold.
    # Con industria + casi (o industria desconocida pero otra dim OK): umbral más alto que antes
    # para no llenar cupo con contactos flojos.
    if not industry_active:
        min_score = max(55, int(fit_threshold) - 15)
    elif tier == ICP_TIER_PERFECT:
        min_score = int(fit_threshold)
    elif industry_unknown:
        # Falta industria pero hay geo/tamaño: exigir score pleno.
        min_score = int(fit_threshold)
    else:
        min_score = max(65, int(fit_threshold) - 10)
    if has_icp and int(score or 0) < min_score:
        return f"ICP score {int(score or 0)} < {min_score}"

    return None


def lead_passes_icp_import_gate(
    lead: Any,
    campaign: Any,
    *,
    fit_threshold: int = 70,
    perfect_only: bool = False,
) -> bool:
    if getattr(lead, "geo_status", None) == "contradictory":
        return False
    emp = getattr(lead, "employee_count", None)
    try:
        emp_i = int(emp) if emp is not None else None
    except (TypeError, ValueError):
        emp_i = None
    reason = icp_import_gate_reason(
        campaign_role=getattr(campaign, "target_role", None),
        campaign_industry=getattr(campaign, "target_industry", None),
        campaign_country=getattr(campaign, "target_country", None),
        campaign_company_size=getattr(campaign, "target_company_size", None),
        prospect_role=getattr(lead, "role", None),
        prospect_industry=getattr(lead, "industry", None),
        prospect_country=getattr(lead, "country", None),
        company_name=getattr(lead, "company_name", None),
        company_domain=getattr(lead, "company_domain", None),
        linkedin_url=getattr(lead, "linkedin_url", None),
        email=getattr(lead, "email", None),
        company_size=getattr(lead, "company_size", None),
        employee_count=emp_i,
        industry_origin=getattr(lead, "industry_origin", None),
        size_origin=getattr(lead, "size_origin", None),
        compatibility_score=getattr(lead, "compatibility_score", None),
        fit_threshold=fit_threshold,
        perfect_only=perfect_only,
    )
    return reason is None
