"""Filtros de ruido y gates ICP estrictos (sector, geo, tamaño + rol)."""

from __future__ import annotations

import re
from typing import Any

from app.services import campaign_icp as icp
from app.services.lead_sourcing.role_alignment import best_icp_role_match

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

# Map ICP tamaño → (min_employees, max_employees) inclusive.
_SIZE_BOUNDS: list[tuple[re.Pattern[str], tuple[int, int]]] = [
    (re.compile(r"\b1[\s\-–]*10\b|micro|startup|seed", re.I), (1, 10)),
    (re.compile(r"\b11[\s\-–]*20\b", re.I), (11, 20)),
    (re.compile(r"\b21[\s\-–]*50\b|pequeñ", re.I), (21, 50)),
    (re.compile(r"\b51[\s\-–]*100\b|mediana|mid[\s\-]?market|smb", re.I), (51, 100)),
    (re.compile(r"\b101[\s\-–]*200\b", re.I), (101, 200)),
    (re.compile(r"\b201[\s\-–]*500\b", re.I), (201, 500)),
    (re.compile(r"\b501[\s\-–]*1000\b|501[\s\-–]*1\s*000", re.I), (501, 1000)),
    (re.compile(r"\b1001|1k\+|enterprise|grande|scale[\s\-]?up", re.I), (1001, 100_000)),
]


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
    score, _ = best_icp_role_match(campaign_role, prospect_role)
    return score >= MIN_ROLE_MATCH_FOR_IMPORT


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def industry_hard_score(prospect_industry: str | None, campaign_industry: str | None) -> tuple[int, str]:
    """Score duro de industria. Desconocido = 0."""
    if campaign_industry is None or icp.is_icp_token_empty(campaign_industry):
        return 100, "ICP industria no configurado"
    pi = _norm(prospect_industry)
    ci = _norm(campaign_industry)
    if not pi:
        return 0, "industria desconocida"
    if pi == ci:
        return 100, "industria coincide"
    if ci in pi or pi in ci:
        return 85, "industria alineada (substring)"
    ci_tokens = {t for t in re.split(r"[\s,/\-]+", ci) if len(t) > 2}
    if not ci_tokens:
        return 0, "industria no alineada"
    hits = sum(1 for t in ci_tokens if t in pi)
    ratio = hits / len(ci_tokens)
    if ratio >= 0.8:
        return 80, f"industria: {hits}/{len(ci_tokens)} términos ICP"
    if ratio >= 0.5:
        return 55, f"industria parcial: {hits}/{len(ci_tokens)} términos"
    return 0, "industria fuera del ICP"


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
    """Extrae (min, max) de headcount a partir de texto y/o número."""
    if employee_count is not None:
        try:
            n = int(employee_count)
            if n > 0:
                return (n, n)
        except (TypeError, ValueError):
            pass
    blob = (company_size or "").strip()
    if not blob:
        return None
    for pattern, bounds in _SIZE_BOUNDS:
        if pattern.search(blob):
            return bounds
    nums = [int(x) for x in re.findall(r"\d+", blob.replace(",", "").replace(".", ""))]
    if len(nums) >= 2:
        lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
        if lo > 0:
            return (lo, hi)
    if len(nums) == 1 and nums[0] > 0:
        return (nums[0], nums[0])
    return None


def icp_size_bounds(campaign_size: str | None) -> tuple[int, int] | None:
    if campaign_size is None or icp.is_icp_token_empty(campaign_size):
        return None
    for pattern, bounds in _SIZE_BOUNDS:
        if pattern.search(campaign_size):
            return bounds
    return parse_employee_bounds(company_size=campaign_size)


def size_ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def size_hard_score(
    *,
    campaign_size: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
) -> tuple[int, str]:
    """Score de tamaño. Desconocido = 40 (casi); fuera de rango = 0; adyacente = 60."""
    target = icp_size_bounds(campaign_size)
    if target is None:
        return 100, "ICP tamaño no configurado"
    actual = parse_employee_bounds(company_size=company_size, employee_count=employee_count)
    if actual is None:
        return 40, "tamaño desconocido"
    if size_ranges_overlap(target, actual):
        return 100, "tamaño alineado con ICP"
    # Banda adyacente = casi perfecto.
    t_lo, t_hi = target
    a_lo, a_hi = actual
    if a_hi < t_lo:
        gap = t_lo - a_hi
    elif a_lo > t_hi:
        gap = a_lo - t_hi
    else:
        gap = 0
    if gap <= max(25, int((t_hi - t_lo + 1) * 0.5)):
        return 60, "tamaño cercano al ICP (banda adyacente)"
    return 0, "tamaño fuera del ICP"


def company_passes_icp_size(
    *,
    campaign_size: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
) -> bool:
    score, _ = size_hard_score(
        campaign_size=campaign_size,
        company_size=company_size,
        employee_count=employee_count,
    )
    return score >= MIN_SIZE_HARD


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
) -> tuple[int, int, str | None]:
    """
    Evalúa identidad ICP.

    Returns:
      (tier, identity_score_0_100, reject_reason|None)
      tier: ICP_TIER_PERFECT | ICP_TIER_NEAR | ICP_TIER_REJECT

    - Perfecto: todas las dims configuradas están y pasan umbral duro.
    - Casi perfecto: dims configuradas presentes; alguna parcial (p.ej. industria).
    - Reject: mismatch conocido; o única dim firmográfica pedida sin dato verificable.
      Tamaño/país ausentes NO rechazan si hay otra dim alineada (Prospeo suele omitirlos).
    """
    ind_score, ind_note = industry_hard_score(prospect_industry, campaign_industry)
    geo_score, geo_note = geo_hard_score(prospect_country, campaign_country)
    size_score, size_note = size_hard_score(
        campaign_size=campaign_company_size,
        company_size=company_size,
        employee_count=employee_count,
    )

    industry_active = campaign_industry is not None and not icp.is_icp_token_empty(campaign_industry)
    geo_active = campaign_country is not None and not icp.is_icp_token_empty(campaign_country)
    size_active = campaign_company_size is not None and not icp.is_icp_token_empty(
        campaign_company_size
    )

    industry_unknown = industry_active and not (prospect_industry or "").strip()
    geo_unknown = geo_active and not (prospect_country or "").strip()
    size_unknown = size_active and parse_employee_bounds(
        company_size=company_size, employee_count=employee_count
    ) is None

    # Mismatch conocido (dato presente pero fuera del ICP).
    if industry_active and ind_score == 0 and (prospect_industry or "").strip():
        return ICP_TIER_REJECT, 0, f"industria no coincide con ICP ({ind_note})"
    if geo_active and not geo_unknown and geo_score < MIN_GEO_HARD:
        return ICP_TIER_REJECT, 0, f"ubicación no coincide con ICP ({geo_note})"
    if size_active and not size_unknown and size_score < MIN_SIZE_HARD:
        return ICP_TIER_REJECT, 0, f"tamaño no coincide con ICP ({size_note})"

    # Industria pedida y ausente: solo si tampoco hay otra dim que aporte evidencia.
    if industry_unknown and not geo_active and not size_active:
        return (
            ICP_TIER_REJECT,
            0,
            "industria desconocida (ICP industria configurada)",
        )

    # Región + industria configuradas: exigir país verificable (calidad > cupo).
    if geo_unknown and industry_active and geo_active:
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
    if industry_active and not industry_unknown:
        active_scores.append(ind_score)
    if geo_active and not geo_unknown:
        active_scores.append(geo_score)
    if size_active and not size_unknown:
        active_scores.append(size_score)

    if not active_scores:
        # Dims configuradas pero sin dato usable en ninguna → casi / cupo vía rol.
        # (Los casos “única dim y desconocida” ya se rechazaron arriba.)
        return ICP_TIER_NEAR, 40, None

    identity = int(round(sum(active_scores) / len(active_scores)))

    perfect = not industry_unknown and not geo_unknown and not size_unknown
    if industry_active and not industry_unknown and ind_score < MIN_INDUSTRY_HARD:
        perfect = False
    if geo_active and not geo_unknown and geo_score < MIN_GEO_HARD:
        perfect = False
    if size_active and not size_unknown and size_score < MIN_SIZE_HARD:
        perfect = False

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
    )
    if tier == ICP_TIER_REJECT:
        return reason or "identidad ICP rechazada"
    if perfect_only and tier != ICP_TIER_PERFECT:
        return reason or "no es match ICP perfecto"
    return None


def icp_lead_rank_key(lead: Any, campaign: Any) -> tuple[int, int, int]:
    """
    Clave de ordenamiento para llenar cupo:
    1) tier más bajo primero (perfecto → casi)
    2) identity score más alto
    3) compatibility_score más alto
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
    )
    compat = int(getattr(lead, "compatibility_score", None) or 0)
    return (tier, -identity, -compat)


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

    if not role_match_passes(campaign_role, prospect_role):
        return f"rol no alinea con ICP (mín. {MIN_ROLE_MATCH_FOR_IMPORT})"

    hard = icp_identity_hard_reason(
        campaign_industry=campaign_industry,
        campaign_country=campaign_country,
        campaign_company_size=campaign_company_size,
        prospect_industry=prospect_industry,
        prospect_country=prospect_country,
        company_size=company_size,
        employee_count=employee_count,
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
                "country": prospect_country,
                "email": email,
                "linkedin_url": linkedin_url,
            },
            campaign_country=campaign_country,
            campaign_industry=campaign_industry,
            campaign_role=campaign_role,
        )

    has_icp = any(
        not icp.is_icp_token_empty(v)
        for v in (campaign_role, campaign_industry, campaign_country, campaign_company_size)
        if v is not None
    )
    # Casi-perfectos: umbral un poco más bajo para poder completar cupo.
    tier, _, _ = assess_icp_identity(
        campaign_industry=campaign_industry,
        campaign_country=campaign_country,
        campaign_company_size=campaign_company_size,
        prospect_industry=prospect_industry,
        prospect_country=prospect_country,
        company_size=company_size,
        employee_count=employee_count,
    )
    industry_active = campaign_industry is not None and not icp.is_icp_token_empty(
        campaign_industry
    )
    industry_unknown = industry_active and not (prospect_industry or "").strip()
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
        compatibility_score=getattr(lead, "compatibility_score", None),
        fit_threshold=fit_threshold,
        perfect_only=perfect_only,
    )
    return reason is None
