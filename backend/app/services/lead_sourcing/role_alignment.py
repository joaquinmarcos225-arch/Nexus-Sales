"""Alineación cargo ICP vs cargo real del contacto — prospección, auditoría y mensajes."""

from __future__ import annotations

import re
from typing import Any, Literal

from app.schemas.mvp_outreach import RoleAlignmentRead
from app.services.lead_sourcing.icp_mapper import _split_titles

AlignmentLevel = Literal["match", "partial", "mismatch", "unknown"]

_STOP = frozenset(
    {
        "of",
        "the",
        "and",
        "for",
        "de",
        "la",
        "el",
        "los",
        "las",
        "y",
        "a",
        "en",
        "del",
    }
)

_SALES_TOKENS = frozenset(
    {
        "sales",
        "revenue",
        "gtm",
        "commercial",
        "comercial",
        "ventas",
        "sdr",
        "bdr",
        "cro",
        "founder",
        "ceo",
        "head",
    }
)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _role_tokens(role: str | None) -> set[str]:
    return {w for w in re.findall(r"\w{3,}", _norm(role)) if w not in _STOP}


def person_role_from_hit(person: dict[str, Any]) -> str:
    for key in ("current_job_title", "job_title", "title", "role"):
        val = person.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    job = person.get("current_job")
    if isinstance(job, dict):
        title = job.get("title") or job.get("job_title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return ""


def icp_role_titles(icp_target_role: str | None) -> list[str]:
    titles = _split_titles(icp_target_role)
    if titles:
        return titles
    cleaned = (icp_target_role or "").strip()
    return [cleaned] if cleaned else []


def role_match_score(icp_role: str | None, prospect_role: str | None) -> int:
    icp = (icp_role or "").strip()
    prospect = (prospect_role or "").strip()
    if not icp or not prospect:
        return 0
    icp_n = _norm(icp)
    pr_n = _norm(prospect)
    if icp_n == pr_n:
        return 100
    if icp_n in pr_n or pr_n in icp_n:
        return 92
    icp_tokens = _role_tokens(icp)
    pr_tokens = _role_tokens(prospect)
    if icp_tokens and pr_tokens:
        overlap = icp_tokens & pr_tokens
        if overlap:
            return min(95, 45 + len(overlap) * 18)
    for token in _SALES_TOKENS:
        if token in icp_n and token in pr_n:
            return 55
    return 0


def best_icp_role_match(
    icp_target_role: str | None,
    prospect_role: str | None,
) -> tuple[int, str]:
    titles = icp_role_titles(icp_target_role)
    if not titles:
        return 0, (icp_target_role or "").strip()
    best_score = 0
    best_title = titles[0]
    for title in titles:
        score = role_match_score(title, prospect_role)
        if score > best_score:
            best_score = score
            best_title = title
    return best_score, best_title


def _alignment_level(score: int) -> AlignmentLevel:
    if score >= 70:
        return "match"
    if score >= 35:
        return "partial"
    if score > 0:
        return "partial"
    return "mismatch"


def decide_selling_to_role(
    icp_target_role: str | None,
    prospect_actual_role: str | None,
    *,
    alignment_level: AlignmentLevel,
    matched_icp_title: str,
) -> tuple[str, str]:
    icp = matched_icp_title or (icp_target_role or "").strip()
    prospect = (prospect_actual_role or "").strip()
    if not prospect:
        return icp, "Sin cargo del contacto — usar rol ICP objetivo."
    if alignment_level == "match":
        return prospect, "Contacto alineado con ICP — vender al cargo real."
    if alignment_level == "partial":
        return prospect, (
            f"Contacto parcialmente alineado con ICP ({icp}). "
            f"Vender al cargo real ({prospect}) sin mezclar otro perfil."
        )
    return prospect, (
        f"ICP objetivo: {icp}. Cargo real: {prospect}. "
        f"Vender ÚNICAMENTE al cargo real ({prospect}); no usar pains de {icp}."
    )


def assess_role_alignment(
    icp_target_role: str | None,
    prospect_actual_role: str | None,
) -> RoleAlignmentRead:
    icp = (icp_target_role or "").strip()
    prospect = (prospect_actual_role or "").strip()
    if not icp and not prospect:
        return RoleAlignmentRead(
            icp_target_role="",
            prospect_actual_role="",
            alignment_level="unknown",
            match_score=0,
            warning=None,
            selling_to_role="",
            selling_rationale="Sin datos de cargo.",
        )
    score, matched_title = best_icp_role_match(icp, prospect)
    level = _alignment_level(score) if icp and prospect else "unknown"
    selling_to, rationale = decide_selling_to_role(
        icp,
        prospect,
        alignment_level=level,
        matched_icp_title=matched_title,
    )
    warning: str | None = None
    if icp and prospect and level == "mismatch":
        warning = (
            f"El cargo encontrado ({prospect}) no coincide con el ICP objetivo ({matched_title or icp}). "
            "Revisá si este contacto es el decisor correcto antes de enviar."
        )
    elif icp and prospect and level == "partial":
        warning = (
            f"Coincidencia parcial entre ICP ({matched_title or icp}) y cargo real ({prospect}). "
            "Verificá que el mensaje no mezcle dos perfiles."
        )
    return RoleAlignmentRead(
        icp_target_role=icp,
        prospect_actual_role=prospect,
        aligned=level == "match",
        alignment_level=level,
        match_score=score,
        warning=warning,
        selling_to_role=selling_to,
        selling_rationale=rationale,
    )


def sort_people_by_icp_role(
    people: list[dict[str, Any]],
    icp_target_role: str | None,
) -> list[dict[str, Any]]:
    if not icp_target_role or not people:
        return people
    titles = icp_role_titles(icp_target_role)

    def _score(person: dict[str, Any]) -> int:
        role = person_role_from_hit(person)
        if not role:
            return 0
        return max(role_match_score(t, role) for t in titles) if titles else 0

    return sorted(people, key=_score, reverse=True)


def role_block_for_prompt(alignment: RoleAlignmentRead) -> str:
    lines = [
        "PERFIL / ROL (OBLIGATORIO — no mezclar):",
        f"  Cargo ICP objetivo de campaña: {alignment.icp_target_role or '—'}",
        f"  Cargo real del contacto: {alignment.prospect_actual_role or '—'}",
        f"  Rol al que vendés este mensaje: {alignment.selling_to_role or '—'}",
        f"  Criterio: {alignment.selling_rationale}",
    ]
    if alignment.warning:
        lines.append(f"  ⚠ ADVERTENCIA: {alignment.warning}")
    lines.append(
        "  Día 1: bloque «por qué escribo» orientado al «Rol al que vendés». "
        "Follow-ups: NO repetir pitch — evolucionar según el día del playbook."
    )
    return "\n".join(lines) + "\n\n"
