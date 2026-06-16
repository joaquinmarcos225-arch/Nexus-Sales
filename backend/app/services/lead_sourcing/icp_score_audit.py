"""Auditoría del score ICP — desglose por dimensión (industria, rol, tamaño, país, señales)."""

from __future__ import annotations

import re
from typing import Any

from app.schemas.mvp_outreach import IcpScoreBreakdownRead
from app.services import campaign_icp as icp
from app.services.lead_sourcing.prospeo_contact_validation import (
    domains_align,
    email_domain,
    is_directory_host,
    is_forbidden_email,
)
from app.services.lead_sourcing.role_alignment import best_icp_role_match


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _icp_active(value: str | None) -> bool:
    return value is not None and not icp.is_icp_token_empty(value)


def _score_country(prospect_country: str | None, campaign_country: str | None) -> tuple[int, str]:
    if not _icp_active(campaign_country):
        return 0, "ICP país no configurado"
    pc = _norm(prospect_country)
    cc = _norm(campaign_country)
    if pc and cc and pc == cc:
        return 100, "País coincide con ICP"
    if pc and cc and (cc in pc or pc in cc):
        return 70, "País parcialmente alineado"
    return 0, "País no alineado con ICP"


def _score_industry(prospect_industry: str | None, campaign_industry: str | None) -> tuple[int, str]:
    if not _icp_active(campaign_industry):
        return 0, "ICP industria no configurado"
    pi = _norm(prospect_industry)
    ci = _norm(campaign_industry)
    if not pi:
        return 0, "Industria del contacto/empresa desconocida"
    if not ci:
        return 0, "ICP industria vacío"
    if pi == ci:
        return 100, "Industria coincide"
    if ci in pi or pi in ci:
        return 75, "Industria parcialmente alineada"
    ci_tokens = {t for t in re.split(r"[\s,/\-]+", ci) if len(t) > 2}
    hits = sum(1 for t in ci_tokens if t in pi)
    if ci_tokens and hits:
        ratio = hits / len(ci_tokens)
        if ratio >= 0.5:
            return int(40 + ratio * 50), f"Industria: {hits}/{len(ci_tokens)} términos ICP"
    return 0, "Industria no alineada"


def _score_company_size(
    company_size: str | None,
    employee_count: int | None,
    campaign_size: str | None,
) -> tuple[int, str]:
    if not _icp_active(campaign_size):
        return 0, "ICP tamaño no configurado"
    blob = " ".join(
        filter(
            None,
            [
                _norm(company_size),
                str(employee_count) if employee_count is not None else "",
            ],
        )
    )
    if not blob.strip():
        return 0, "Tamaño de empresa desconocido"
    cs = _norm(campaign_size)
    if cs in blob or blob in cs:
        return 100, "Tamaño alineado"
    size_tokens = re.findall(r"\d+", cs)
    emp_tokens = re.findall(r"\d+", blob)
    if size_tokens and emp_tokens:
        target = int(size_tokens[0])
        actual = int(emp_tokens[0])
        if target and abs(actual - target) / max(target, 1) <= 0.35:
            return 85, "Tamaño numérico cercano al ICP"
    for hint in ("startup", "enterprise", "mid", "pequeñ", "mediana", "grande"):
        if hint in cs and hint in blob:
            return 80, f"Segmento '{hint}' alineado"
    return 0, "Tamaño no alineado con ICP"


def _score_role(prospect_role: str | None, campaign_role: str | None) -> tuple[int, str]:
    if not _icp_active(campaign_role):
        return 0, "ICP cargo no configurado"
    if not (prospect_role or "").strip():
        return 0, "Cargo del contacto desconocido"
    score, matched = best_icp_role_match(campaign_role, prospect_role)
    if score >= 70:
        return score, f"Cargo alineado con ICP ({matched})"
    if score >= 35:
        return score, f"Coincidencia parcial de cargo ({matched})"
    return score, f"Cargo distinto al ICP objetivo ({matched or campaign_role})"


def _score_additional_signals(
    *,
    email: str | None,
    linkedin_url: str | None,
    company_domain: str | None,
    company_icp_score: int | None,
) -> tuple[int, str]:
    pts = 0
    parts: list[str] = []
    dom = (company_domain or "").strip().lower().removeprefix("www.")
    em_dom = email_domain(email)
    if email and dom and em_dom and not is_forbidden_email(email) and not is_directory_host(dom):
        if domains_align(dom, em_dom):
            pts += 45
            parts.append("email corporativo en dominio")
        elif em_dom:
            pts += 20
            parts.append(f"email @{em_dom}")
    if linkedin_url:
        pts += 15
        parts.append("LinkedIn personal")
    if company_icp_score is not None and company_icp_score > 0:
        company_pts = min(40, int(company_icp_score * 0.4))
        pts += company_pts
        parts.append(f"empresa ICP base {company_icp_score}% → +{company_pts}")
    return min(100, pts), (" · ".join(parts) if parts else "Sin señales adicionales")


def compute_icp_score_breakdown(
    *,
    campaign_industry: str | None,
    campaign_country: str | None,
    campaign_role: str | None,
    campaign_company_size: str | None,
    prospect_industry: str | None,
    prospect_country: str | None,
    prospect_role: str | None,
    company_size: str | None = None,
    employee_count: int | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
    company_domain: str | None = None,
    company_icp_relevance_score: int | None = None,
    legacy_compatibility_score: int | None = None,
) -> IcpScoreBreakdownRead:
    """
    Score ICP a nivel contacto (no solo empresa).
    El rol tiene peso alto; mismatch fuerte limita el score final.
    """
    industry_score, industry_note = _score_industry(prospect_industry, campaign_industry)
    role_score, role_note = _score_role(prospect_role, campaign_role)
    country_score, country_note = _score_country(prospect_country, campaign_country)
    size_score, size_note = _score_company_size(company_size, employee_count, campaign_company_size)
    additional_score, additional_note = _score_additional_signals(
        email=email,
        linkedin_url=linkedin_url,
        company_domain=company_domain,
        company_icp_score=company_icp_relevance_score,
    )

    dimensions: list[tuple[str, int, int, bool]] = [
        ("industry", industry_score, 25, _icp_active(campaign_industry)),
        ("role", role_score, 30, _icp_active(campaign_role)),
        ("country", country_score, 15, _icp_active(campaign_country)),
        ("company_size", size_score, 10, _icp_active(campaign_company_size)),
        ("additional", additional_score, 20, True),
    ]

    active = [(name, score, weight) for name, score, weight, on in dimensions if on]
    if not active:
        weighted = 55
        formula = "Sin ICP estricto configurado → base 55"
    else:
        total_w = sum(w for _, _, w in active)
        weighted = int(round(sum(s * w for _, s, w in active) / total_w))
        formula = " + ".join(f"{s}×{w}" for _, s, w in active) + f" / {total_w}"

    final = max(0, min(100, weighted))

    notes: list[str] = [
        f"Industria ({industry_score}%): {industry_note}",
        f"Cargo ({role_score}%): {role_note}",
        f"País ({country_score}%): {country_note}",
        f"Tamaño ({size_score}%): {size_note}",
        f"Señales ({additional_score}%): {additional_note}",
    ]

    role_mismatch_cap_applied = False
    if _icp_active(campaign_role) and role_score < 35:
        capped = min(final, int(role_score * 0.6 + final * 0.4))
        if capped < final:
            role_mismatch_cap_applied = True
            notes.append(
                f"Tope por mismatch de rol: {final}% → {capped}% "
                f"(cargo ICP ≠ cargo contacto; score cargo={role_score}%)"
            )
            final = capped

    if legacy_compatibility_score is not None and legacy_compatibility_score > final + 15:
        notes.append(
            f"Score legacy ({legacy_compatibility_score}%) sobrevaloraba el contacto "
            f"— usaba sobre todo encaje de empresa, no de cargo."
        )

    return IcpScoreBreakdownRead(
        industry_score=industry_score,
        role_score=role_score,
        company_size_score=size_score,
        country_score=country_score,
        additional_signals_score=additional_score,
        final_score=final,
        company_only_score=company_icp_relevance_score,
        legacy_compatibility_score=legacy_compatibility_score,
        role_mismatch_cap_applied=role_mismatch_cap_applied,
        notes=notes,
        formula_explanation=formula,
    )


def breakdown_from_profile_and_campaign(
    campaign: Any,
    profile: Any,
    *,
    company_row: Any | None = None,
    legacy_compatibility_score: int | None = None,
) -> IcpScoreBreakdownRead:
    """Conveniencia: Campaign + LeadProfileRead + fila empresa opcional."""
    person = profile.person
    company = profile.company
    row = company_row
    return compute_icp_score_breakdown(
        campaign_industry=getattr(campaign, "target_industry", None),
        campaign_country=getattr(campaign, "target_country", None),
        campaign_role=getattr(campaign, "target_role", None),
        campaign_company_size=getattr(campaign, "target_company_size", None),
        prospect_industry=company.industry if company else None,
        prospect_country=getattr(row, "country", None) if row else None,
        prospect_role=person.role if person else None,
        company_size=getattr(row, "company_size", None) if row else (company.size if company else None),
        employee_count=getattr(row, "employee_count", None) if row else None,
        email=person.email if person else None,
        linkedin_url=person.linkedin_url if person else None,
        company_domain=company.domain if company else None,
        company_icp_relevance_score=(
            getattr(row, "icp_relevance_score", None) if row else company.icp_score
        ),
        legacy_compatibility_score=legacy_compatibility_score or (
            company.icp_score if company else None
        ),
    )
