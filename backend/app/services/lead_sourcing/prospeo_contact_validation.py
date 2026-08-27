"""Validación: contactos Prospeo deben pertenecer a la empresa objetivo, no al directorio fuente."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.services.lead_sourcing.company_name_normalizer import normalize_company_name
from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

# Rechazo obligatorio por dominio de email (exacto tras @).
MANDATORY_REJECT_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "crunchbase.com",
        "wellfound.com",
        "linkedin.com",
        "angellist.com",
        "angel.co",
    }
)

# Dominios de directorios / marketplaces — no son empleadores reales del ICP.
DIRECTORY_HOST_FRAGMENTS: tuple[str, ...] = (
    "crunchbase.com",
    "wellfound.com",
    "angel.co",
    "angellist.com",
    "linkedin.com",
    "clutch.co",
    "g2.com",
    "producthunt.com",
    "capterra.com",
    "getapp.com",
    "softwareadvice.com",
    "trustradius.com",
    "glassdoor.com",
    "indeed.com",
    "zoominfo.com",
    "apollo.io",
    "lusha.com",
    "rocketreach.co",
    "datanyze.com",
    "owler.com",
    "pitchbook.com",
    "dealroom.co",
    "f6s.com",
    "builtin.com",
    "techstars.com",
    "ycombinator.com",
    # Listas / directorios SEO (no empleadores ICP)
    "growthlist.co",
    "growthlist.com",
    "saasworthy.com",
    "saashub.com",
    "g2crowd.com",
    "craft.co",
    "cbinsights.com",
)

_GENERIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
    }
)


@dataclass(frozen=True)
class ProspeoContactValidation:
    ok: bool
    reason: str
    target_company: str
    target_domain: str | None
    detected_company: str | None
    detected_domain: str | None
    email_domain: str | None
    person_name: str | None

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "target_company": self.target_company,
            "target_domain": self.target_domain,
            "detected_company": self.detected_company,
            "detected_domain": self.detected_domain,
            "email_domain": self.email_domain,
            "person_name": self.person_name,
        }


def is_directory_host(host: str | None) -> bool:
    h = (host or "").strip().lower().removeprefix("www.")
    if not h:
        return False
    return any(frag in h for frag in DIRECTORY_HOST_FRAGMENTS)


def is_prospeo_searchable_domain(host: str | None) -> bool:
    """Dominio usable en search-person (ASCII, no directorio, no vacío)."""
    h = (host or "").strip().lower().removeprefix("www.")
    if not h or "." not in h:
        return False
    if is_directory_host(h):
        return False
    try:
        h.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def is_consumer_email_domain(domain: str | None) -> bool:
    dom = (domain or "").strip().lower().removeprefix("www.")
    return bool(dom) and dom in _GENERIC_EMAIL_DOMAINS


def email_domain(email: str | None) -> str | None:
    raw = (email or "").strip().lower()
    if "@" not in raw:
        return None
    dom = raw.split("@", 1)[1].strip()
    return dom.removeprefix("www.") if dom else None


def is_forbidden_email(email: str | None) -> bool:
    """Email de directorio — no importar, no mostrar, no contar."""
    dom = email_domain(email)
    if not dom:
        return False
    if dom in MANDATORY_REJECT_EMAIL_DOMAINS:
        return True
    return is_directory_host(dom)


def domains_align(expected: str | None, actual: str | None) -> bool:
    e = (expected or "").strip().lower().removeprefix("www.")
    a = (actual or "").strip().lower().removeprefix("www.")
    if not e or not a:
        return False
    if e == a:
        return True
    return a.endswith("." + e) or e.endswith("." + a)


def _normalize_tokens(name: str) -> set[str]:
    n = normalize_company_name(name) or (name or "").strip()
    n = re.sub(r"[^\w\s]", " ", n.lower())
    tokens = {t for t in n.split() if len(t) > 1}
    return tokens


def company_names_match(target: str, detected: str) -> bool:
    a = (normalize_company_name(target) or (target or "").strip()).lower()
    b = (normalize_company_name(detected) or (detected or "").strip()).lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    ta, tb = _normalize_tokens(target), _normalize_tokens(detected)
    if not ta or not tb:
        return False
    overlap = ta & tb
    if not overlap:
        return False
    # «Aidetic» vs «Aidetic SAS» — al menos un token fuerte compartido
    min_len = min(len(ta), len(tb))
    return len(overlap) >= max(1, min_len - 1)


def extract_person_employer(person: dict[str, Any]) -> tuple[str | None, str | None]:
    name: str | None = None
    domain: str | None = None

    job = person.get("current_job")
    if isinstance(job, dict):
        comp = job.get("company")
        if isinstance(comp, dict):
            name = (comp.get("name") or comp.get("company_name") or "").strip() or name
            domain = (
                _website_domain(comp.get("website"))
                or _website_domain(comp.get("domain"))
                or _website_domain(comp.get("company_website"))
                or domain
            )

    for key in ("company_name", "current_company_name", "employer_name", "organization_name"):
        if not name:
            val = person.get(key)
            if isinstance(val, str) and val.strip():
                name = val.strip()

    comp = person.get("company")
    if isinstance(comp, dict):
        if not name:
            name = (comp.get("name") or comp.get("company_name") or "").strip() or None
        domain = (
            domain
            or _website_domain(comp.get("website"))
            or _website_domain(comp.get("domain"))
            or _website_domain(comp.get("company_website"))
        )

    org = person.get("organization")
    if isinstance(org, dict):
        if not name:
            name = (org.get("name") or "").strip() or None
        domain = domain or _website_domain(org.get("website") or org.get("primary_domain"))

    return name, domain


def resolve_target_company_domain(
    *,
    website_url: str | None,
    company_domain: str | None = None,
    firmo: dict[str, Any] | None = None,
) -> str | None:
    """Dominio corporativo real para búsqueda Prospeo (nunca crunchbase/wellfound)."""
    candidates: list[str] = []
    for raw in (company_domain, _website_domain(website_url)):
        if raw and not is_directory_host(raw):
            candidates.append(raw.lower().removeprefix("www."))
    if isinstance(firmo, dict):
        for key in ("website", "company_website", "domain", "primary_domain"):
            fd = _website_domain(firmo.get(key) if isinstance(firmo.get(key), str) else None)
            if fd and not is_directory_host(fd):
                candidates.append(fd)
    for c in candidates:
        if c:
            return c
    return None


def validate_prospeo_contact(
    *,
    target_company_name: str,
    target_domain: str | None,
    person: dict[str, Any],
    email: str | None,
    person_name: str | None = None,
) -> ProspeoContactValidation:
    target = (target_company_name or "").strip() or "Empresa"
    detected_name, detected_domain = extract_person_employer(person)
    em_dom = email_domain(email)
    display_name = person_name or (
        f"{person.get('first_name') or ''} {person.get('last_name') or ''}".strip()
        or person.get("full_name")
        or person.get("name")
    )

    base = ProspeoContactValidation(
        ok=False,
        reason="",
        target_company=target,
        target_domain=target_domain,
        detected_company=detected_name,
        detected_domain=detected_domain,
        email_domain=em_dom,
        person_name=str(display_name).strip() if display_name else None,
    )

    if not target_domain:
        return replace(
            base, reason="Empresa sin dominio corporativo (solo directorio o sin web)"
        )

    if is_forbidden_email(email):
        return replace(
            base,
            reason=f"Email prohibido (@{em_dom}) — no pertenece a {target}",
        )

    if em_dom and is_directory_host(em_dom):
        return replace(base, reason=f"Email de directorio/marketplace (@{em_dom})")

    if detected_domain and is_directory_host(detected_domain):
        return replace(
            base, reason=f"Empleador detectado es directorio ({detected_domain})"
        )

    if em_dom and not domains_align(target_domain, em_dom):
        if em_dom in _GENERIC_EMAIL_DOMAINS:
            return replace(
                base,
                reason=f"Email personal (@{em_dom}), no corporativo de {target_domain}",
            )
        return replace(
            base,
            reason=(
                f"Dominio email @{em_dom} no coincide con empresa objetivo ({target_domain})"
            ),
        )

    if detected_name and not company_names_match(target, detected_name):
        return replace(
            base,
            reason=(
                f"Empresa del contacto «{detected_name}» no coincide con objetivo «{target}»"
            ),
        )

    if detected_domain and not domains_align(target_domain, detected_domain):
        return replace(
            base,
            reason=(
                f"Dominio empleador {detected_domain} no coincide con objetivo {target_domain}"
            ),
        )

    # Sin empleador explícito: exigir email corporativo alineado
    if not detected_name and em_dom and domains_align(target_domain, em_dom):
        return replace(base, ok=True, reason="ok_email_domain")

    if detected_name and company_names_match(target, detected_name):
        if detected_domain and domains_align(target_domain, detected_domain):
            return replace(base, ok=True, reason="ok")
        if em_dom and domains_align(target_domain, em_dom):
            return replace(base, ok=True, reason="ok")
        if not em_dom and not detected_domain:
            return replace(
                base,
                reason="Empleador sin dominio verificable y sin email corporativo",
            )

    return replace(base, reason="Contacto no verificable para la empresa objetivo")
