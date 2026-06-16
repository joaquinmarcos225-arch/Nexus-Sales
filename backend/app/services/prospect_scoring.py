"""Scoring determinístico: fit inicial + interés conversacional."""

from collections.abc import Mapping

from app.models.enums import ProspectStatus
from app.services import campaign_icp as icp


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _icp_usable_campaign_field(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip()
    return not icp.is_icp_token_empty(text)


def score_prospect_against_campaign(
    prospect_fields: Mapping[str, str | None],
    *,
    campaign_country: str | None,
    campaign_industry: str | None,
    campaign_role: str | None,
) -> tuple[int, int, ProspectStatus]:
    """
    Devuelve (compatibility_score 0–100, interest_probability 0–100, estado inicial clase).
    """
    pts = 0
    max_pts = 0

    p_country = _norm(prospect_fields.get("country"))
    p_industry = _norm(prospect_fields.get("industry"))
    p_role = _norm(prospect_fields.get("role"))

    if _icp_usable_campaign_field(campaign_country):
        max_pts += 35
        if p_country and p_country == _norm(campaign_country):
            pts += 35

    if _icp_usable_campaign_field(campaign_industry):
        max_pts += 35
        ci = _norm(campaign_industry)
        if p_industry and ci:
            if p_industry == ci:
                pts += 35
            elif ci in p_industry or p_industry in ci:
                pts += 24

    if _icp_usable_campaign_field(campaign_role):
        max_pts += 30
        tr = _norm(campaign_role)
        if tr and tr in p_role:
            pts += 30

    if max_pts == 0:
        compatibility = 55
    else:
        compatibility = int(round(100 * pts / max_pts))

    compatibility = max(0, min(100, compatibility))

    bonus = 0
    if prospect_fields.get("email"):
        bonus += 8
    if prospect_fields.get("linkedin_url"):
        bonus += 5

    interest = compatibility - 12 + bonus
    interest = max(0, min(100, interest))

    status = (
        ProspectStatus.compatible
        if compatibility >= 70
        else ProspectStatus.not_compatible
    )

    return compatibility, interest, status


def explain_compatibility(
    prospect_fields: Mapping[str, str | None],
    *,
    campaign_country: str | None,
    campaign_industry: str | None,
    campaign_role: str | None,
    product_name: str | None = None,
) -> tuple[int, str]:
    parts: list[str] = []
    compat, _, _ = score_prospect_against_campaign(
        prospect_fields,
        campaign_country=campaign_country,
        campaign_industry=campaign_industry,
        campaign_role=campaign_role,
    )
    p_country = _norm(prospect_fields.get("country"))
    p_industry = _norm(prospect_fields.get("industry"))
    p_role = _norm(prospect_fields.get("role"))
    if _icp_usable_campaign_field(campaign_country):
        parts.append(
            "pais alineado" if p_country and p_country == _norm(campaign_country) else "pais fuera ICP"
        )
    if _icp_usable_campaign_field(campaign_industry):
        ci = _norm(campaign_industry)
        ok = p_industry and ci and (p_industry == ci or ci in p_industry or p_industry in ci)
        parts.append("industria alineada" if ok else "industria parcial/no alineada")
    if _icp_usable_campaign_field(campaign_role):
        tr = _norm(campaign_role)
        parts.append("rol alineado" if tr and tr in p_role else "rol no alineado")
    if product_name:
        parts.append(f"producto: {product_name}")
    if not parts:
        parts.append("sin ICP estricto, score basado en datos disponibles")
    return compat, "; ".join(parts)


def score_prospect_breakdown(
    prospect_fields: Mapping[str, str | None],
    *,
    campaign_country: str | None,
    campaign_industry: str | None,
    campaign_role: str | None,
) -> tuple[int, str, dict[str, int | str | bool]]:
    """
    Igual que score_prospect_against_campaign, con desglose para debug Lead Sourcing.
    """
    pts = 0
    max_pts = 0
    country_pts = 0
    industry_pts = 0
    role_pts = 0

    p_country = _norm(prospect_fields.get("country"))
    p_industry = _norm(prospect_fields.get("industry"))
    p_role = _norm(prospect_fields.get("role"))

    country_active = _icp_usable_campaign_field(campaign_country)
    industry_active = _icp_usable_campaign_field(campaign_industry)
    role_active = _icp_usable_campaign_field(campaign_role)

    if country_active:
        max_pts += 35
        if p_country and p_country == _norm(campaign_country):
            country_pts = 35
            pts += 35

    if industry_active:
        max_pts += 35
        ci = _norm(campaign_industry)
        if p_industry and ci:
            if p_industry == ci:
                industry_pts = 35
                pts += 35
            elif ci in p_industry or p_industry in ci:
                industry_pts = 24
                pts += 24

    if role_active:
        max_pts += 30
        from app.services.lead_sourcing.role_alignment import best_icp_role_match

        role_score, _ = best_icp_role_match(campaign_role, prospect_fields.get("role"))
        role_pts = int(round(30 * role_score / 100))
        pts += role_pts

    if max_pts == 0:
        compatibility = 55
        score_mode = "no_strict_icp"
    else:
        compatibility = int(round(100 * pts / max_pts))
        score_mode = "weighted_icp"

    compatibility = max(0, min(100, compatibility))
    email_bonus = 8 if prospect_fields.get("email") else 0
    linkedin_bonus = 5 if prospect_fields.get("linkedin_url") else 0

    explain_parts: list[str] = []
    if country_active:
        explain_parts.append(
            f"país {country_pts}/35"
            if country_pts
            else "país 0/35"
        )
    if industry_active:
        explain_parts.append(f"industria {industry_pts}/35")
    if role_active:
        explain_parts.append(f"rol {role_pts}/30")
    if not explain_parts:
        explain_parts.append("ICP campaña vacío → base 55")
    if email_bonus:
        explain_parts.append(f"+{email_bonus} email")
    if linkedin_bonus:
        explain_parts.append(f"+{linkedin_bonus} linkedin (solo interés)")

    breakdown: dict[str, int | str | bool] = {
        "country_pts": country_pts,
        "industry_pts": industry_pts,
        "role_pts": role_pts,
        "max_pts": max_pts,
        "email_bonus": email_bonus,
        "linkedin_bonus": linkedin_bonus,
        "score_mode": score_mode,
        "prospect_country": p_country or "",
        "prospect_industry": p_industry or "",
        "prospect_role": p_role or "",
        "campaign_country": _norm(campaign_country),
        "campaign_industry": _norm(campaign_industry),
        "campaign_role": _norm(campaign_role),
    }
    return compatibility, "; ".join(explain_parts), breakdown


def compute_interest_probability(
    *,
    current_status: str,
    prior_interest_level: str | None,
    objection_type: str | None,
    inbound_count: int,
    asks_questions: bool,
    wants_meeting: bool,
    last_inbound_text: str | None,
    days_since_last_inbound: float | None,
) -> tuple[int, str]:
    score = 20
    reasons: list[str] = []
    txt = (last_inbound_text or "").lower()

    if current_status == ProspectStatus.failed.value:
        return 6, "contacto fallido o error tecnico"
    if current_status == ProspectStatus.not_interested.value:
        return 10, "declaro no interes"
    if current_status == ProspectStatus.meeting_booked.value:
        return 98, "reunion agendada"

    if inbound_count > 0:
        score += min(25, inbound_count * 9)
        reasons.append(f"{inbound_count} respuestas")
    else:
        score = 18
        reasons.append("sin respuesta")

    il = (prior_interest_level or "low").lower()
    if il == "high":
        score += 28
        reasons.append("interes alto detectado")
    elif il == "medium":
        score += 14
        reasons.append("interes medio detectado")

    if wants_meeting or "reuni" in txt:
        score = max(score, 92)
        reasons.append("pidio reunion")
    elif "me interesa" in txt or "interesa" in txt:
        score = max(score, 82)
        reasons.append("expreso interes")
    elif objection_type == "send_info":
        score = max(score, 60)
        score = min(score, 70)
        reasons.append("pidio info")
    elif objection_type in ("now_not_priority", "now_not_time"):
        score = max(30, min(45, score))
        reasons.append("ahora no / no prioridad")
    elif objection_type == "not_interested":
        score = min(score, 15)
        reasons.append("no interesado")

    if asks_questions or "?" in txt:
        score += 9
        reasons.append("hizo pregunta concreta")

    if days_since_last_inbound is not None and inbound_count == 0:
        if days_since_last_inbound > 5:
            score -= 10
            reasons.append("silencio prolongado")
        elif days_since_last_inbound > 2:
            score -= 5

    score = max(0, min(100, int(round(score))))
    return score, ", ".join(reasons) if reasons else "score por señales conversacionales"
