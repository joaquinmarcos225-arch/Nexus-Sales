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
        "ae",
        "account",
    }
)

# Tokens demasiado genéricos: no cuentan como overlap de rol (evita Head of Design ≈ Head of Sales).
_WEAK_ROLE_TOKENS = frozenset(
    {
        "head",
        "director",
        "manager",
        "lead",
        "chief",
        "senior",
        "junior",
        "vp",
        "vice",
        "president",
        "officer",
        "global",
        "regional",
        "country",
        "team",
        "ops",
        "operations",
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


def _has_sales_signal(norm: str, tokens: set[str]) -> bool:
    """Señal de ventas/GTM en EN o ES (sales ↔ comercial/ventas)."""
    if tokens & _SALES_TOKENS:
        return True
    return any(token in norm for token in _SALES_TOKENS)


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
    icp_tokens = _role_tokens(icp) - _WEAK_ROLE_TOKENS
    pr_tokens = _role_tokens(prospect) - _WEAK_ROLE_TOKENS
    if icp_tokens and pr_tokens:
        overlap = icp_tokens & pr_tokens
        if overlap:
            return min(95, 45 + len(overlap) * 18)
    # Soft match ventas/GTM cross-idioma (Head of Sales ≈ Director Comercial).
    if _has_sales_signal(icp_n, icp_tokens) and _has_sales_signal(pr_n, pr_tokens):
        return 70
    # Soft match ejecutivo (CEO ≈ Founder / Director General / Propietario).
    if _has_exec_signal(icp_n, icp_tokens) and _has_exec_signal(pr_n, pr_tokens):
        return 72
    return 0


# Títulos extra para búsqueda Prospeo cuando el ICP es de ventas.
_SALES_PROSPEO_ALIASES: tuple[str, ...] = (
    "Head of Sales",
    "VP of Sales",
    "Director of Sales",
    "Sales Director",
    "Director Comercial",
    "Director de Ventas",
    "Gerente Comercial",
    "Gerente de Ventas",
    "Chief Revenue Officer",
    "Head of Revenue",
)

# CEO / dueño / dirección general — inmobiliarias y SMB suelen no tener "CEO" en Prospeo.
_EXEC_PROSPEO_ALIASES: tuple[str, ...] = (
    "CEO",
    "Chief Executive Officer",
    "Founder",
    "Co-Founder",
    "Cofounder",
    "Owner",
    "Propietario",
    "Dueño",
    "Director General",
    "Gerente General",
    "Managing Director",
    "President",
    "Presidente",
)

_EXEC_ROLE_RE = re.compile(
    r"\b("
    r"ceo|chief\s+executive|founder|co[\s\-]?founder|owner|"
    r"propietario|dueñ[oa]|director\s+general|gerente\s+general|"
    r"managing\s+director|president|presidente|socio\s+fundador"
    r")\b",
    re.I,
)


def _has_exec_signal(norm: str, tokens: set[str]) -> bool:
    if tokens & {"ceo", "founder", "owner", "presidente", "president", "propietario"}:
        return True
    return bool(_EXEC_ROLE_RE.search(norm))


def prospeo_role_title_includes(icp_target_role: str | None) -> list[str]:
    """Títulos a pedir a Prospeo (ICP + aliases ES/EN si es rol de ventas o ejecutivo)."""
    titles = icp_role_titles(icp_target_role)
    out: list[str] = []
    seen: set[str] = set()

    def _add(title: str) -> None:
        t = (title or "").strip()
        if not t:
            return
        key = _norm(t)
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    for t in titles:
        _add(t)
    title_norms = [_norm(t) for t in titles]
    title_token_sets = [_role_tokens(t) - _WEAK_ROLE_TOKENS for t in titles]
    if any(
        _has_sales_signal(n, toks) for n, toks in zip(title_norms, title_token_sets, strict=False)
    ):
        for alias in _SALES_PROSPEO_ALIASES:
            _add(alias)
    if any(
        _has_exec_signal(n, toks) for n, toks in zip(title_norms, title_token_sets, strict=False)
    ):
        for alias in _EXEC_PROSPEO_ALIASES:
            _add(alias)
    return out[:10]


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
