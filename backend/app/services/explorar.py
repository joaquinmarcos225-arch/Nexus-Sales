"""Explorar: búsqueda de personas estilo LinkedIn sobre Prospeo.

Cobro:
- Ver resultados no cobra por página, pero las páginas nuevas se acumulan por empresa;
  cada ``PAGES_PER_CREDIT`` páginas (≈ costo Prospeo de 1 crédito Nexus) se descuenta 1 crédito.
- Revelar email + celular de una persona = 1 crédito. Si cualquier cliente de Nexus ya la reveló,
  queda revelada para todos y no se cobra.
- Sumar a campaña una persona revelada (y pagada) por la empresa no vuelve a cobrar; si el revelado
  fue gratis, sumarla a campaña cuesta 1 crédito (nunca se paga dos veces por la misma persona).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_logger = logging.getLogger("nexus.explorar")

# Wall-clock del search() en curso (para _fetch_page sin romper monkeypatches de tests).
_search_deadline_mono: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "explorar_search_deadline_mono", default=None
)

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models.explorar import ExplorarFavorite, ExplorarReveal, ExplorarSearchCache, ExplorarUsage
from app.services.credits import CreditError, consume_explorar_credit, get_user_available_credits
from app.services.explorar_filter_catalog import (
    DEPARTMENT_OPTIONS,
    HEADCOUNT_OPTIONS,
    INDUSTRY_OPTIONS,
    SENIORITY_OPTIONS,
    filter_catalog,
    sanitize_industry_values,
)
from app.services.lead_sourcing.location_check import assess_person_geo, country_allowed
from app.services.lead_sourcing.role_alignment import (
    feminine_title_variants,
    title_matches,
    title_matches_loose,
    title_synonym_variants,
)

PROSPEO_PAGE_SIZE = 25
CACHE_TTL = timedelta(hours=24)


class ExplorarError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def pages_per_credit() -> int:
    try:
        return max(1, int(os.getenv("NEXUS_EXPLORAR_PAGES_PER_CREDIT", "10")))
    except ValueError:
        return 10


def max_fetches_per_request(filters: dict[str, Any] | None = None) -> int:
    """Tope de páginas Prospeo por request.

    Con filtros duros (depto/tamaño/país) el keep-rate baja: hay que recorrer más
    páginas para armar volumen **sin** aflojar el post-filtro de calidad.
    El techo real lo pone ``search_deadline_sec`` (evita 504 del middleware HTTP).
    """
    try:
        base = max(1, int(os.getenv("NEXUS_EXPLORAR_MAX_FETCHES_PER_REQUEST", "12")))
    except ValueError:
        base = 12
    if not filters:
        return base
    strict = bool(
        filters.get("person_department")
        or filters.get("company_headcount_range")
        or filters.get("person_location_search")
        or filters.get("company_location_search")
    )
    if not strict:
        return base
    try:
        boosted = max(1, int(os.getenv("NEXUS_EXPLORAR_STRICT_MAX_FETCHES", "20")))
    except ValueError:
        boosted = 20
    return max(base, boosted)


def search_deadline_sec() -> float:
    """Wall-clock por request HTTP. Explorar es no-kill en middleware; dar margen para llenar página."""
    try:
        return max(8.0, min(float(os.getenv("NEXUS_EXPLORAR_SEARCH_DEADLINE_SEC", "32")), 55.0))
    except ValueError:
        return 32.0


def _clean_list(values: list[str] | None, *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        v = str(raw or "").strip()
        if not v or v.lower() in seen:
            continue
        seen.add(v.lower())
        out.append(v[:120])
        if len(out) >= limit:
            break
    return out


MAX_TITLE_FILTERS = 20


_ENGLISH_TITLE_RE = re.compile(r"\b(of|manager|head|officer|coordinator)\b|\bdirector$", re.IGNORECASE)


def _feminine_if_spanish(title: str) -> list[str]:
    return [] if _ENGLISH_TITLE_RE.search(title.strip()) else feminine_title_variants(title)


def expand_titles(titles: list[str]) -> list[str]:
    """Cargos pedidos primero; después sus formas femeninas y sinónimos, repartidos entre cargos."""
    extras_per_title = [
        _clean_list(
            _feminine_if_spanish(t)
            + [v for syn in title_synonym_variants(t) for v in [syn, *_feminine_if_spanish(syn)]],
            limit=MAX_TITLE_FILTERS,
        )
        for t in titles
    ]
    out = list(titles)
    for depth in range(MAX_TITLE_FILTERS):
        for extras in extras_per_title:
            if depth < len(extras):
                out.append(extras[depth])
    return _clean_list(out, limit=MAX_TITLE_FILTERS)


def build_filters(
    *,
    names: list[str] | None = None,
    titles: list[str] | None = None,
    location: str | None = None,
    company_hq: str | None = None,
    industries: list[str] | None = None,
    seniorities: list[str] | None = None,
    companies: list[str] | None = None,
    departments: list[str] | None = None,
    headcounts: list[str] | None = None,
    lookalike_company_oids: list[str] | None = None,
    require_email: bool = False,
    require_mobile: bool = False,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}

    name_list = [n for n in _clean_list(names, limit=10) if 2 <= len(n) <= 100]
    li_urls = _linkedin_profile_urls_from_texts(name_list)
    if li_urls:
        filters["_linkedin_profile_urls"] = li_urls[:3]
    plain_names = [n for n in name_list if not _LINKEDIN_IN_RE.search(n)]
    if plain_names:
        # Variantes sin tilde: Prospeo a veces indexa "Jose" y el tester escribe "José".
        expanded: list[str] = []
        seen_n: set[str] = set()
        for n in plain_names:
            for variant in _person_name_variants(n):
                key = variant.lower()
                if key in seen_n:
                    continue
                seen_n.add(key)
                expanded.append(variant)
        filters["person_name"] = {"include": expanded[:12]}

    # Cargos elegidos + equivalentes exactos (mismo puesto en otro idioma/nombre de área).
    # Nada de alias amplios («Owner» para cualquier jefe): traían otros roles.
    title_list = _clean_list(titles, limit=10)
    if title_list:
        filters["person_job_title"] = {"include": expand_titles(title_list), "match_mode": "CONTAINS"}

    loc = (location or "").strip()
    if loc:
        from app.services.lead_sourcing.b2c_person_search import (
            _location_queries,
            resolve_canonical_locations,
        )

        locations = resolve_canonical_locations(loc, max_locations=6)
        if not locations:
            locations = _location_queries(loc) or [loc]
        filters["person_location_search"] = {"include": locations[:6]}

    from app.services.lead_sourcing.location_check import company_hq_search_filter

    filters.update(company_hq_search_filter(company_hq, max_locations=6))

    seniority_list = [s for s in _clean_list(seniorities, limit=9) if s in SENIORITY_OPTIONS]
    if seniority_list:
        filters["person_seniority"] = {"include": seniority_list}

    department_list = [d for d in _clean_list(departments, limit=11) if d in DEPARTMENT_OPTIONS]
    if department_list:
        filters["person_department"] = {"include": department_list}

    headcount_list = [h for h in _clean_list(headcounts, limit=11) if h in HEADCOUNT_OPTIONS]
    if headcount_list:
        filters["company_headcount_range"] = headcount_list

    industry_list = sanitize_industry_values(industries, limit=6)
    if industry_list:
        # Prospeo ignora en silencio company.industry; el filtro válido es de primer nivel.
        filters["company_industry"] = {"include": industry_list}
    company: dict[str, Any] = {}
    company_list = _clean_list(companies, limit=5)
    sites = [c.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
             for c in company_list if "." in c and " " not in c]
    names = [c for c in company_list if c not in sites and not ("." in c and " " not in c)]
    if sites:
        company["websites"] = {"include": sites}
    if names:
        company["names"] = {"include": names}
    if company:
        filters["company"] = company

    from app.services.explorar_lookalike import lookalike_filter

    alike = lookalike_filter(lookalike_company_oids or [])
    if alike:
        filters["company_lookalike"] = alike

    # Contacto: NO achicar el universo a AND mail+cel (deja docenas en vez de miles).
    # Preferencia mail+cel la arma search_plan; acá solo un canal si pidieron uno solo.
    if require_email and require_mobile:
        pass
    elif require_email or require_mobile:
        from app.services.lead_sourcing.prospeo_phone import contact_details_filter

        filters.update(
            contact_details_filter(require_mobile=require_mobile, require_email=require_email)
        )
    return filters


# Prospeo mete Customer Success dentro de Sales; quien filtra Ventas no lo espera.
_DEPARTMENT_EXCLUDE = {"Sales": frozenset({"customer success"})}

# Si el tester tilda depto/tamaño → obligatorios (van a Prospeo + post-filtro).
# Industria/seniority siguen “blandos” (mayoría si hay varios).
_HARD_OPTIONAL_KEYS = frozenset({"person_department", "company_headcount_range"})
_SOFT_FILTER_KEYS = frozenset({"company_industry", "person_seniority"})
_SOFT_PROSPEO_PRIORITY = ("company_industry", "person_seniority")
_SOFT_PROSPEO_MAX = 2
_SOFT_MATCH_STATUSES = frozenset({"exact", "equivalent", "similar"})

# Cargo típico cuando LinkedIn no marca bien el depto (ej. HR).
_DEPT_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Human Resources": (
        "hr",
        "rrhh",
        "human resources",
        "recursos humanos",
        "people",
        "talent",
        "talento",
        "chro",
        "hrbp",
        "people ops",
        "people operations",
        "chief people",
        "cultura",
        "recruiting",
        "reclutamiento",
        "seleccion",
        "selección",
    ),
    "Sales": (
        "sales",
        "ventas",
        "comercial",
        "commercial",
        "account executive",
        "revenue",
        "business development",
        "bdr",
        "sdr",
    ),
    "Marketing": ("marketing", "growth", "brand", "demanda", "demand gen"),
    "Finance": ("finance", "finanzas", "cfo", "controller", "tesoreria", "tesorería", "contab"),
    "Operations": ("operations", "operaciones", "coo", "ops "),
    "Information Technology": ("information technology", " cio", "sistemas", "infraestructura", "it manager", "it director"),
    "Engineering & Technical": ("engineer", "ingenier", "cto", "technical"),
    "Product": ("product", "producto", "cpo", "product manager", "product owner"),
    "Design": ("design", "diseño", "designer", "ux", "ui"),
    "Legal": ("legal", "legales", "counsel", "abogad"),
    "C-Suite": ("ceo", "cfo", "coo", "cto", "cmo", "chro", "founder", "co-founder", "director general"),
}


_LINKEDIN_IN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/([A-Za-z0-9_%\-]+)/?",
    re.IGNORECASE,
)


def _person_name_variants(name: str) -> list[str]:
    raw = (name or "").strip()
    if not raw:
        return []
    out = [raw]
    folded = _norm_ascii_lower(raw)
    # Title-case folded for Prospeo ("jose garcia" → "Jose Garcia")
    titled = " ".join(p.capitalize() for p in folded.split())
    if titled and titled.lower() != raw.lower():
        out.append(titled)
    return out


def _linkedin_profile_urls_from_texts(texts: list[str] | None) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in texts or []:
        for m in _LINKEDIN_IN_RE.finditer(str(raw or "")):
            slug = (m.group(1) or "").strip().strip("/")
            if not slug:
                continue
            url = f"https://www.linkedin.com/in/{slug}"
            key = slug.lower()
            if key in seen:
                continue
            seen.add(key)
            urls.append(url)
    return urls


def is_name_primary_search(filters: dict[str, Any]) -> bool:
    """Nombre (o link /in/) sin cargo/industria/depto/tamaño/empresa → búsqueda de persona."""
    if not filters.get("person_name") and not filters.get("_linkedin_profile_urls"):
        return False
    structured = (
        "person_job_title",
        "company_industry",
        "person_seniority",
        "person_department",
        "company_headcount_range",
        "company",
        "company_lookalike",
    )
    return not any(k in filters for k in structured)


def soft_filter_keys(filters: dict[str, Any]) -> list[str]:
    return [k for k in _SOFT_PROSPEO_PRIORITY if k in filters]


def soft_filters_active(filters: dict[str, Any]) -> bool:
    """Varios blandos (industria/seniority) → modo mayoría."""
    return len(soft_filter_keys(filters)) >= 2


def soft_keys_sent_to_prospeo(filters: dict[str, Any]) -> list[str]:
    keys = soft_filter_keys(filters)
    if len(keys) < 2:
        return keys
    return keys[:_SOFT_PROSPEO_MAX]


# Familia manufactura: el chip «Manufactura» solo manda General Manufacturing (~docenas en AR+HR+Mgr).
# Solo labels del catálogo Explorar (Search Person) — ampliar = más volumen sin salir del ICP fabril.
_INDUSTRY_PROSPEO_EXPAND: dict[str, tuple[str, ...]] = {
    "General Manufacturing": (
        "General Manufacturing",
        "Food and Beverage Manufacturing",
        "Motor Vehicle Manufacturing",
        "Pharmaceutical Manufacturing",
        "Personal Care Product Manufacturing",
        "Consumer Goods",
        "Breweries",
        "Cosmetics",
    ),
    # Contractia / salud: el chip «Salud» solo manda Hospitals (~bajo volumen en AR+RRHH).
    "Hospitals and Health Care": (
        "Hospitals and Health Care",
        "Medical Practices",
        "Pharmaceutical Manufacturing",
        "Medical Equipment Manufacturing",
        "Biotechnology Research",
        "Mental Health Care",
        "Wellness and Fitness Services",
    ),
    "Financial Services": (
        "Financial Services",
        "Banking",
        "Insurance",
        "Investment Management",
        "Capital Markets",
    ),
}

# Gerente en UI → Prospeo también Head/Director (sigue siendo liderazgo; ranking prioriza Manager).
_SENIORITY_PROSPEO_EXPAND: dict[str, tuple[str, ...]] = {
    "Manager": ("Manager", "Head", "Director"),
    "Head": ("Head", "Director", "Manager"),
    "Director": ("Director", "Head", "Vice President", "Manager"),
}


def expand_industries_for_prospeo(industries: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in industries or []:
        label = str(raw or "").strip()
        if not label:
            continue
        group = _INDUSTRY_PROSPEO_EXPAND.get(label, (label,))
        for item in group:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:12]


def expand_seniorities_for_prospeo(seniorities: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in seniorities or []:
        label = str(raw or "").strip()
        if not label:
            continue
        group = _SENIORITY_PROSPEO_EXPAND.get(label, (label,))
        for item in group:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:8]


def core_filters_for_prospeo(filters: dict[str, Any]) -> dict[str, Any]:
    """Pedido a Prospeo: ancho para volumen; calidad en post-filtro local.

    - Búsqueda por nombre pura: solo ``person_name`` (ignora región pegada del UI).
    - Depto **sí** va a Prospeo: si no, pedís RRHH+Gerente+industria y Prospeo
      devuelve gerentes de cualquier área; el post-filtro deja 2–4 de 25.
    - Industria «Manufactura» se expande a la familia fabril (si no, ~79 vs miles).
    - Seniority Gerente se expande a Head/Director en el pedido (ranking prioriza exacto).
    - Tamaño sí va. Industria/seniority blandos: con 2+, solo los top a Prospeo.
    """
    if is_name_primary_search(filters) and filters.get("person_name"):
        return {"person_name": filters["person_name"]}
    out = {
        k: v
        for k, v in filters.items()
        if k not in {"_linkedin_profile_urls"}
    }
    if soft_filters_active(filters):
        keep_soft = set(soft_keys_sent_to_prospeo(filters))
        for k in _SOFT_FILTER_KEYS:
            if k not in keep_soft:
                out.pop(k, None)
    ind = out.get("company_industry")
    if isinstance(ind, dict) and ind.get("include"):
        expanded = expand_industries_for_prospeo(list(ind["include"]))
        if expanded:
            out["company_industry"] = {"include": expanded}
    sen = out.get("person_seniority")
    if isinstance(sen, dict) and sen.get("include"):
        expanded_s = expand_seniorities_for_prospeo(list(sen["include"]))
        if expanded_s:
            out["person_seniority"] = {"include": expanded_s}
    return out


def volume_scope_filters(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """De más estricto a más amplio para llenar volumen con keep-rate usable.

    Tras el rechazo duro de industria contradictoria, soltar industria en Prospeo
    deja keep-rate ~0% (IT/consultora no pasa) y solo quema fetches. Por eso:

    1) Filtros completos (industria + seniority expandidas + depto).
    2) Sin depto si hace falta (Founder/C-Suite, o depto muy estricto): sigue
       industria+nivel — ej. Chile restaurantes founders ~300.
    3) Sin seniority en Prospeo pero **con industria** (familia Manager ya expandida;
       sirve si el pozo con nivel exacto es chico).

    Nunca un scope solo-país / solo-depto cuando pediste industria.
    """
    tight = core_filters_for_prospeo(filters)
    scopes = [tight]
    sen = filters.get("person_seniority")
    sen_labels = {
        str(s).strip()
        for s in ((sen or {}).get("include") or [])
        if isinstance(sen, dict) and str(s).strip()
    }
    wants_industry = bool(filters.get("company_industry"))

    def _add(scope: dict[str, Any]) -> None:
        if scope in scopes or scope == scopes[-1]:
            return
        if wants_industry and "company_industry" not in scope:
            return
        scopes.append(scope)

    # Soltar depto: dueños no vienen en Ops; también si hay industria+depto (pozo chico).
    # No soltar seniority acá: Entry/Junior contaminan y el hard-reject los tira (keep-rate bajo).
    if filters.get("person_department") and (
        sen_labels & {"Founder/Owner", "C-Suite"}
        or (wants_industry and (filters.get("person_seniority") or filters.get("person_job_title")))
    ):
        no_dept = dict(tight)
        no_dept.pop("person_department", None)
        _add(no_dept)
    return scopes


def soft_majority_threshold(soft_n: int) -> int:
    """Al menos la mitad (ceil/floor): 2→1, 3→2, 4→2.

    Antes 2→2 exigía industria Y seniority: sin dato de industria tumba Managers RRHH
    válidos y deja la grilla en ~16. Ranking pone primero a los que aciertan ambos.
    """
    if soft_n <= 0:
        return 0
    if soft_n == 1:
        return 1
    return soft_n // 2


def _norm_ascii_lower(text: str | None) -> str:
    raw = str(text or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ü", "u"), ("ñ", "n")):
        raw = raw.replace(a, b)
    return re.sub(r"\s+", " ", raw).strip()


def _role_matches_department(role: str | None, wanted: list[str]) -> bool:
    blob = _norm_ascii_lower(role)
    if not blob:
        return False
    for dept in wanted:
        for kw in _DEPT_ROLE_KEYWORDS.get(str(dept), ()):
            needle = _norm_ascii_lower(kw)
            if needle and needle in blob:
                return True
    return False


def _department_soft_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool | None:
    """True/False si hay señal de depto en el hit; None = sin depto listado."""
    dept_filter = filters.get("person_department")
    if not isinstance(dept_filter, dict) or not dept_filter.get("include"):
        return None
    hit_depts = {d.lower() for d in (hit.get("departments") or [])}
    if not hit_depts:
        return None
    wanted = list(dept_filter["include"])
    excluded = set().union(*(_DEPARTMENT_EXCLUDE.get(d, frozenset()) for d in wanted))
    if excluded and hit_depts <= excluded:
        return False
    wanted_l = {str(d).strip().lower() for d in wanted if str(d).strip()}
    if not wanted_l:
        return None
    if hit_depts & wanted_l:
        return True
    return False


def department_hard_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Depto tildado: match de depto LinkedIn o cargo típico del área. Si no, fuera."""
    dept_filter = filters.get("person_department")
    if not isinstance(dept_filter, dict) or not dept_filter.get("include"):
        return True
    # Founder/C-Suite: LinkedIn casi nunca etiqueta Operations/Sales; no tumbar dueños.
    sen = filters.get("person_seniority")
    if isinstance(sen, dict):
        wanted_sen = {str(s).strip() for s in (sen.get("include") or []) if str(s).strip()}
        hit_sen = str(hit.get("seniority") or hit.get("person_seniority") or "").strip()
        if hit_sen in {"Founder/Owner", "C-Suite"} and wanted_sen & {"Founder/Owner", "C-Suite"}:
            return True
    wanted = list(dept_filter["include"])
    signal = _department_soft_ok(hit, filters)
    if signal is True:
        return True
    if signal is False and hit.get("departments"):
        # Exclude duro (ej. Customer Success en Sales) — no rescatar por cargo ambiguo.
        excluded = set().union(*(_DEPARTMENT_EXCLUDE.get(d, frozenset()) for d in wanted))
        hit_depts = {d.lower() for d in (hit.get("departments") or [])}
        if excluded and hit_depts <= excluded:
            return False
    return _role_matches_department(hit.get("role"), wanted)


def size_hard_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Tamaño tildado: tiene que coincidir; 1000+ vs pyme = fuera. Sin dato = fuera.

    `review` (dato observado sin procedencia) se muestra para que el SDR revise;
    no confirma firmográficos, pero no se tira del grid.
    """
    if "company_headcount_range" not in filters:
        return True
    firm = assess_explorar_firmographics(hit, filters)
    return firm.size.status in _SOFT_MATCH_STATUSES | {"review"}


def location_hard_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Región tildada: sin país de persona → fuera; país distinto → fuera. No usa sede empresa."""
    loc_filter = filters.get("person_location_search")
    if not isinstance(loc_filter, dict) or not loc_filter.get("include"):
        return True
    country = (hit.get("country") or "").strip()
    if not country:
        return False
    locations = list(loc_filter.get("include") or [])
    if not country_allowed(country, locations):
        return False
    if _explorar_geo(hit, filters).status == "contradictory":
        return False
    return True


def company_hq_hard_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """HQ tildado: país empresa conocido y fuera → fuera. Sin país empresa → pasa."""
    from app.services.lead_sourcing.location_check import company_hq_matches

    hq_filter = filters.get("company_location_search")
    if not isinstance(hq_filter, dict) or not hq_filter.get("include"):
        return True
    includes = [str(x).strip() for x in (hq_filter.get("include") or []) if str(x).strip()]
    if not includes:
        return True
    joined = ", ".join(includes)
    return company_hq_matches(
        company_country=(hit.get("company_country") or "").strip() or None,
        campaign_hq=joined,
    )


def _seniority_labels(filters: dict[str, Any], *, expanded: bool) -> set[str]:
    sen = filters.get("person_seniority")
    if not isinstance(sen, dict) or not sen.get("include"):
        return set()
    raw = [str(s).strip() for s in sen["include"] if str(s).strip()]
    if expanded:
        raw = expand_seniorities_for_prospeo(raw)
    return {s.lower() for s in raw}


def _seniority_soft_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool | None:
    """True/False/None. Acepta familia expandida (Manager→Head/Director) para no tumbar volumen."""
    if not _seniority_labels(filters, expanded=False):
        return None
    raw = hit.get("seniority") or hit.get("person_seniority")
    if raw in (None, ""):
        return None
    return str(raw).strip().lower() in _seniority_labels(filters, expanded=True)


def _seniority_exact_ok(hit: dict[str, Any], filters: dict[str, Any]) -> bool | None:
    labels = _seniority_labels(filters, expanded=False)
    if not labels:
        return None
    raw = hit.get("seniority") or hit.get("person_seniority")
    if raw in (None, ""):
        return None
    return str(raw).strip().lower() in labels


def score_soft_filters(hit: dict[str, Any], filters: dict[str, Any]) -> tuple[int, int]:
    """(matched, total) solo industria/seniority. Depto/tamaño son duros aparte."""
    matched = 0
    total = 0
    firm = None

    if "company_industry" in filters:
        total += 1
        firm = firm or assess_explorar_firmographics(hit, filters)
        if firm.industry.status in _SOFT_MATCH_STATUSES:
            matched += 1

    if "person_seniority" in filters:
        total += 1
        if _seniority_soft_ok(hit, filters) is True:
            matched += 1

    return matched, total


def hit_matches_core_filters(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Cargo + región + depto/tamaño si están tildados. Siempre AND.

    Búsqueda por nombre pura: no aplicar región/depto/tamaño (si no, un LATAM
    pegado del UI tira a la persona aunque esté en LinkedIn).
    """
    if is_name_primary_search(filters):
        return True
    title_filter = filters.get("person_job_title")
    if isinstance(title_filter, dict) and title_filter.get("include"):
        role = (hit.get("role") or "").strip()
        if not role or not title_matches_loose(role, list(title_filter["include"])):
            return False
    if not location_hard_ok(hit, filters):
        return False
    if not company_hq_hard_ok(hit, filters):
        return False
    if not department_hard_ok(hit, filters):
        return False
    if not size_hard_ok(hit, filters):
        return False
    return True


def hit_matches_filters(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not hit_matches_core_filters(hit, filters):
        return False
    # Pedís industria X → nada que grite sector Y (Explorar = misma regla que campaña).
    wanted_ind = _wanted_industries(filters)
    if wanted_ind:
        from app.services.lead_sourcing.firmographics import sector_mismatch_reason

        if sector_mismatch_reason(
            ", ".join(wanted_ind),
            company_name=_str(hit.get("company_name")),
            role=_str(hit.get("role")),
            industry=_str(hit.get("industry")),
        ):
            return False
    # Seniority explícitamente otra (Entry vs Manager) → fuera.
    if "person_seniority" in filters and _seniority_soft_ok(hit, filters) is False:
        return False
    if "company_industry" in filters:
        firm = assess_explorar_firmographics(hit, filters)
        # Contradictoria es dura siempre: soft-majority 1/2 dejaba IT/consultora
        # entrar en Manufactura solo por seniority Manager.
        if firm.industry.status == "contradictory":
            return False
    if soft_filters_active(filters):
        matched, total = score_soft_filters(hit, filters)
        # Industria sin dato no suma al total exigido (si no, 2 blandos → umbral 2 tumba todo).
        if "company_industry" in filters:
            firm = assess_explorar_firmographics(hit, filters)
            if firm.industry.status in {"incomplete", "review"}:
                total = max(0, total - 1)
        if total <= 0:
            return True
        return matched >= soft_majority_threshold(total)
    return True


def annotate_filter_fit(hit: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    out = dict(hit)
    matched, total = score_soft_filters(hit, filters)
    out["filter_matched"] = matched
    out["filter_scored"] = total
    out["filter_total_soft"] = total
    # Exacto (Manager) para ranking; familia expandida ya pasó el filtro.
    out["seniority_match"] = _seniority_exact_ok(hit, filters)
    out["seniority_family_match"] = _seniority_soft_ok(hit, filters)
    return out


def _hit_usable_without_strict_title(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Rescate 1ª página: mismo piso que core (cargo + geo + depto/tamaño)."""
    return hit_matches_core_filters(hit, filters)


_FIRMO_RANK = {
    "exact": 5,
    "equivalent": 4,
    "similar": 3,
    "review": 2,
    "incomplete": 1,
    "contradictory": 0,
}

FIRMO_STATUS_LABELS = {
    "exact": "coincide",
    "equivalent": "equivalente",
    "similar": "similar",
    "incomplete": "información incompleta",
    "review": "revisar",
    "contradictory": "no coincide",
}

COMBINED_STATUS_LABELS = {
    "confirmed": "coincide",
    "similar": "similar",
    "incomplete": "información incompleta",
    "review": "revisar",
    "contradictory": "no compatible",
}


def _wanted_industries(filters: dict[str, Any]) -> list[str]:
    ind = filters.get("company_industry")
    if not isinstance(ind, dict):
        return []
    raw = [str(x).strip() for x in (ind.get("include") or []) if str(x).strip()]
    # Misma familia que mandamos a Prospeo → Food/Auto/etc. cuentan como fit de Manufactura.
    return expand_industries_for_prospeo(raw) or raw


def _wanted_headcounts(filters: dict[str, Any]) -> list[str]:
    raw = filters.get("company_headcount_range")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _hit_employee_count(hit: dict[str, Any]) -> int | None:
    raw = hit.get("employee_count")
    if raw is None:
        size = hit.get("company_size")
        if isinstance(size, int):
            raw = size
        else:
            return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _explorar_origins(hit: dict[str, Any]) -> tuple[str | None, str | None]:
    """Hits de búsqueda Prospeo: origen observado. None explícito = histórico, no se inventa."""
    ind_o = hit.get("industry_origin") if "industry_origin" in hit else "prospeo"
    sz_o = hit.get("size_origin") if "size_origin" in hit else "prospeo"
    return ind_o, sz_o


def _best_wanted_label(wanted: list[str], score_fn) -> str | None:
    if not wanted:
        return None
    best_label = wanted[0]
    best_rank = -1
    for label in wanted:
        dim = score_fn(label)
        rank = _FIRMO_RANK.get(getattr(dim, "status", None), -1)
        if rank > best_rank:
            best_rank = rank
            best_label = label
        if rank >= _FIRMO_RANK["equivalent"]:
            break
    return best_label


def _hit_size_fields(hit: dict[str, Any]) -> tuple[str | None, int | None]:
    size = hit.get("company_size")
    count = _hit_employee_count(hit)
    if isinstance(size, int):
        return None, int(size) if size else count
    return size, count


def assess_explorar_firmographics(hit: dict[str, Any], filters: dict[str, Any]):
    """Evalúa industria/tamaño del hit contra los chips de Explorar (no campaign.target_*)."""
    from app.services.lead_sourcing.firmographics import assess_company_firmographics

    origin_i, origin_s = _explorar_origins(hit)
    size_text, count = _hit_size_fields(hit)
    wanted_ind = _wanted_industries(filters)
    wanted_sz = _wanted_headcounts(filters)
    campaign_industry = _best_wanted_label(
        wanted_ind,
        lambda w: assess_company_firmographics(
            campaign_industry=w,
            industry=hit.get("industry"),
            industry_origin=origin_i,
        ).industry,
    )
    campaign_size = _best_wanted_label(
        wanted_sz,
        lambda w: assess_company_firmographics(
            campaign_company_size=w,
            company_size=size_text,
            employee_count=count,
            size_origin=origin_s,
        ).size,
    )
    return assess_company_firmographics(
        campaign_industry=campaign_industry,
        campaign_company_size=campaign_size,
        industry=hit.get("industry"),
        industry_origin=origin_i,
        company_size=size_text,
        employee_count=count,
        size_origin=origin_s,
    )


def annotate_explorar_firmographics(hit: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    """Adjunta estados de industria/tamaño sin inventar el dato observado."""
    firm = assess_explorar_firmographics(hit, filters)
    out = dict(hit)
    origin_i, origin_s = _explorar_origins(hit)
    if "industry_origin" not in out:
        out["industry_origin"] = origin_i
    if "size_origin" not in out:
        out["size_origin"] = origin_s
    out["industry_status"] = firm.industry.status
    out["industry_reason"] = firm.industry.reason
    out["industry_status_label"] = FIRMO_STATUS_LABELS.get(firm.industry.status or "")
    out["size_status"] = firm.size.status
    out["size_reason"] = firm.size.reason
    out["size_status_label"] = FIRMO_STATUS_LABELS.get(firm.size.status or "")
    out["firmographic_status"] = firm.combined
    out["firmographic_reason"] = firm.combined_reason
    out["firmographic_status_label"] = COMBINED_STATUS_LABELS.get(firm.combined or "")
    return out


def _industry_allowed(industry: str | None, wanted: list[str]) -> bool:
    """Compat de tests viejos: delega en el clasificador central."""
    return assess_explorar_firmographics(
        {"industry": industry},
        {"company_industry": {"include": wanted}},
    ).industry.status != "contradictory"


def _headcount_bounds(label: str) -> tuple[int, int] | None:
    s = label.strip().replace(",", "")
    if s.endswith("+") and s[:-1].isdigit():
        return int(s[:-1]), 10**9
    lo, sep, hi = s.partition("-")
    if sep and lo.isdigit() and hi.isdigit():
        return int(lo), int(hi)
    return None


def _headcount_allowed(company_size: Any, wanted: list[str]) -> bool:
    if not wanted or company_size in (None, ""):
        return True
    size = str(company_size).strip()
    if size in HEADCOUNT_OPTIONS:
        return size in wanted
    try:
        n = int(float(size.replace(",", "")))
    except ValueError:
        return True
    for label in wanted:
        b = _headcount_bounds(label)
        if b and b[0] <= n <= b[1]:
            return True
    return False


def has_targeting(filters: dict[str, Any]) -> bool:
    return any(
        k in filters
        for k in (
            "person_name",
            "_linkedin_profile_urls",
            "person_job_title",
            "person_location_search",
            "company_location_search",
            "company",
            "company_lookalike",
            "company_industry",
            "person_seniority",
            "person_department",
            "company_headcount_range",
        )
    )


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_usage(db: Session, company_id: int) -> ExplorarUsage:
    row = db.get(ExplorarUsage, company_id)
    if row is None:
        row = ExplorarUsage(company_id=company_id, pages_unbilled=0, pages_total=0, day=_today(), pages_today=0)
        db.add(row)
        db.flush()
    if row.day != _today():
        row.day = _today()
        row.pages_today = 0
    return row


def usage_summary(db: Session, company_id: int, user_id: int) -> dict[str, Any]:
    usage = get_usage(db, company_id)
    per_credit = pages_per_credit()
    return {
        "pages_unbilled": int(usage.pages_unbilled),
        "pages_per_credit": per_credit,
        "pages_until_charge": max(0, per_credit - int(usage.pages_unbilled)),
        "pages_today": int(usage.pages_today),
        "credits_available": int(get_user_available_credits(db, company_id, user_id)),
    }


def _cache_key(company_id: int, filters: dict[str, Any], page: int) -> str:
    raw = json.dumps({"c": company_id, "f": filters, "p": page}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cache(db: Session, key: str) -> dict[str, Any] | None:
    row = db.get(ExplorarSearchCache, key)
    if row is None:
        return None
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created is not None and datetime.now(timezone.utc) - created > CACHE_TTL:
        db.delete(row)
        db.flush()
        return None
    try:
        return json.loads(row.payload_json)
    except ValueError:
        return None


def _write_cache(db: Session, company_id: int, key: str, payload: dict[str, Any]) -> None:
    cutoff = datetime.now(timezone.utc) - CACHE_TTL
    db.execute(delete(ExplorarSearchCache).where(ExplorarSearchCache.created_at < cutoff))
    existing = db.get(ExplorarSearchCache, key)
    if existing is not None:
        db.delete(existing)
        db.flush()
    db.add(ExplorarSearchCache(cache_key=key, company_id=company_id, payload_json=json.dumps(payload)))
    db.flush()


def _str(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _status_of(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return _str(obj.get("status"))
    return None


def parse_hit(row: dict[str, Any]) -> dict[str, Any] | None:
    person = row.get("person") if isinstance(row.get("person"), dict) else row
    if not isinstance(person, dict):
        return None
    org = row.get("company") if isinstance(row.get("company"), dict) else None
    if org is None:
        org = person.get("company") if isinstance(person.get("company"), dict) else {}

    pid = _str(person.get("person_id") or row.get("person_id") or person.get("id"))
    if not pid:
        return None
    first = _str(person.get("first_name"))
    last = _str(person.get("last_name"))
    name = _str(person.get("full_name")) or " ".join(p for p in (first, last) if p) or None
    if not name:
        return None
    job = person.get("current_job") if isinstance(person.get("current_job"), dict) else {}
    role = _str(person.get("current_job_title") or person.get("job_title") or job.get("title") or person.get("headline"))

    loc = person.get("location") if isinstance(person.get("location"), dict) else {}
    country = _str(loc.get("country") or person.get("country"))
    city = _str(loc.get("city") or person.get("city"))

    domain = _str(org.get("domain") or org.get("website") or person.get("company_domain"))
    if domain:
        domain = domain.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
    email_status = (_status_of(person.get("email")) or "").upper()
    mobile_status = (_status_of(person.get("mobile")) or "").upper()
    history = person.get("job_history") if isinstance(person.get("job_history"), list) else []
    current = next((j for j in history if isinstance(j, dict) and j.get("current")), None)
    departments = [str(d) for d in (current or {}).get("departments") or [] if d]
    seniority = _str(
        person.get("seniority")
        or person.get("person_seniority")
        or job.get("seniority")
        or (current or {}).get("seniority")
    )
    # Señales extra: Prospeo a veces marca has_mobile / preview sin status AVAILABLE.
    from app.services.lead_sourcing.prospeo_phone import person_indicates_mobile_available

    email_ok = email_status in ("VERIFIED", "AVAILABLE", "FOUND", "PRESENT")
    if not email_ok:
        for key in ("has_email", "email_available"):
            if person.get(key) in (True, 1, "1", "true", "True"):
                email_ok = True
                break
    mobile_ok = mobile_status in ("VERIFIED", "AVAILABLE", "FOUND", "PRESENT")
    if not mobile_ok:
        mobile_ok = person_indicates_mobile_available(person)
    return {
        "person_id": pid,
        "source_person_id": pid,
        "source_provider": "prospeo",
        "name": name[:255],
        "first_name": first,
        "last_name": last,
        "role": role[:255] if role else None,
        "company_name": _str(org.get("name") or person.get("company_name")),
        "company_domain": domain,
        "industry": _str(org.get("industry")),
        "industry_origin": "prospeo",
        "company_size": _str(org.get("employee_range") or org.get("company_size") or org.get("employee_count")),
        "size_origin": "prospeo",
        "country": country,
        "city": city,
        "company_country": _str(
            (org.get("location") or {}).get("country")
            if isinstance(org.get("location"), dict)
            else None
        )
        or _str(org.get("country") or org.get("hq_country")),
        "linkedin_url": _str(person.get("linkedin_url") or person.get("linkedin")),
        "has_email": email_ok,
        "has_mobile": mobile_ok,
        "email_verified": email_status == "VERIFIED",
        "mobile_verified": mobile_status == "VERIFIED",
        "departments": departments,
        "seniority": seniority,
    }


# Prospeo corta pedidos seguidos con 429; el último valor no se usa (es el intento final).
_RATE_LIMIT_WAITS = (1.5, 3.0, 0.0)


def _fetch_page(filters: dict[str, Any], page: int) -> dict[str, Any]:
    from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
    from app.services.lead_sourcing.providers.prospeo_mvp import _PROSPEO_SEARCH_PERSON, _post_json_result

    deadline_mono = _search_deadline_mono.get()
    try:
        for attempt, wait in enumerate(_RATE_LIMIT_WAITS):
            try:
                result = _post_json_result(_PROSPEO_SEARCH_PERSON, {"page": page, "filters": filters})
                break
            except ProviderAPIError as e:
                limited = e.status_code == 429 or (e.error_code or "").upper() == "RATE_LIMITED"
                if not limited or attempt == len(_RATE_LIMIT_WAITS) - 1:
                    raise
                # No dormir más allá del wall-clock del request (evita 504 / hilos zombi).
                if deadline_mono is not None and wait > 0:
                    left = deadline_mono - time.monotonic() - 1.0
                    if left <= 0.05:
                        raise ExplorarError(
                            "Demasiadas búsquedas seguidas. Esperá unos segundos.",
                            status_code=429,
                        ) from e
                    time.sleep(min(wait, left))
                else:
                    time.sleep(wait)
    except ExplorarError:
        raise
    except ProviderNotConfiguredError as e:
        raise ExplorarError(str(e), status_code=503) from e
    except ProviderAPIError as e:
        code = (e.error_code or "").upper()
        if code == "INVALID_FILTERS":
            raise ExplorarError("Algún filtro no es válido. Probá con otros valores.") from e
        if e.status_code == 429 or code == "RATE_LIMITED":
            raise ExplorarError("Demasiadas búsquedas seguidas. Esperá unos segundos.", status_code=429) from e
        raise ExplorarError("No pudimos completar la búsqueda. Probá de nuevo.", status_code=502) from e

    payload = result.payload or {}
    raw_rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    hits = [h for h in (parse_hit(r) for r in raw_rows if isinstance(r, dict)) if h]
    # Si Prospeo filtró por mail/celular VERIFIED, confiar en el filtro aunque el payload
    # no traiga status (si no, la UI muestra «Celular: No» mentiroso).
    details = filters.get("person_contact_details") if isinstance(filters, dict) else None
    if isinstance(details, dict):
        want_email = details.get("email") == ["VERIFIED"]
        want_mobile = details.get("mobile") == ["VERIFIED"]
        if want_email or want_mobile:
            stamped: list[dict[str, Any]] = []
            for h in hits:
                row = dict(h)
                if want_email:
                    row["has_email"] = True
                    row["email_verified"] = True
                if want_mobile:
                    row["has_mobile"] = True
                    row["mobile_verified"] = True
                stamped.append(row)
            hits = stamped
    pag = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
    total = pag.get("total_count")
    total_pages = pag.get("total_page") or pag.get("total_pages")
    return {
        "results": hits,
        "total_count": int(total) if isinstance(total, (int, float)) else len(hits),
        "total_pages": int(total_pages) if isinstance(total_pages, (int, float)) else (1 if hits else 0),
    }


_PERSON_ID_PREFIXES = ("role-prospeo-", "b2c-prospeo-")


def person_id_from_external_id(external_id: str | None) -> str | None:
    ext = (external_id or "").strip()
    if ext.startswith("explorar-"):
        return ext[len("explorar-"):] or None
    for prefix in _PERSON_ID_PREFIXES:
        if ext.startswith(prefix):
            # {campaign_id}-{person_id}-{idx}
            parts = ext[len(prefix):].split("-")
            if len(parts) >= 3:
                return "-".join(parts[1:-1]) or None
    return None


_EXPLORAR_PROVIDERS = frozenset({"prospeo", "brave", "nexus_cache", "web_search"})


def explorar_source_identity(
    snapshot: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    """(provider, source_person_id, source_external_id). No inventa IDs."""
    snap = snapshot or {}
    pid = str(snap.get("source_person_id") or snap.get("person_id") or "").strip()[:128]
    raw_provider = str(snap.get("source_provider") or "").strip().lower()[:32]
    provider = raw_provider if raw_provider in _EXPLORAR_PROVIDERS else None
    if not pid:
        return provider, None, None
    return provider or "prospeo", pid, pid


def apply_in_use(
    results: list[dict[str, Any]],
    index: Any,
    *,
    keep_blocked: bool,
) -> list[dict[str, Any]]:
    """
    Saca a quien la empresa está prospectando ahora (persona o su empresa) y a quien pidió la baja.
    Con `keep_blocked` (búsqueda por nombre) los deja, marcados con `in_use` para mostrar el aviso.
    """
    out = []
    for r in results:
        block = index.blocking(r)
        if block is not None:
            if keep_blocked:
                out.append({**r, "in_use": block})
            continue
        item = dict(r)
        if index.contacted_before(person_id=r.get("person_id"), linkedin_url=r.get("linkedin_url")):
            item["contacted_before"] = True
        out.append(item)
    return out


def find_used_prospect(
    db: Session,
    *,
    company_id: int,
    person_id: str | None,
    linkedin_url: str | None = None,
    email: str | None = None,
    phone: str | None = None,
):
    """Prospecto de la empresa que ya es esta persona (en cualquier campaña), o None."""
    from app.models.prospect import Prospect
    from app.services.prospect_ingestion import find_duplicate_in_company

    pid = str(person_id or "").strip()
    if pid:
        by_person_id = db.scalars(
            select(Prospect).where(
                Prospect.company_id == int(company_id),
                Prospect.source_person_id == pid,
            )
        ).first()
        if by_person_id is not None:
            return by_person_id
        by_external = db.scalars(
            select(Prospect).where(
                Prospect.company_id == int(company_id),
                Prospect.source_external_id == pid,
            )
        ).first()
        if by_external is not None:
            return by_external
        for row in db.scalars(
            select(Prospect).where(
                Prospect.company_id == int(company_id),
                Prospect.source_external_id.like(f"%{pid}%"),
            )
        ).all():
            if person_id_from_external_id(row.source_external_id) == pid:
                return row
    return find_duplicate_in_company(
        db,
        company_id=int(company_id),
        linkedin_url=linkedin_url,
        email=email,
        phone=phone,
        whatsapp=phone,
    )


def _attach_reveals(db: Session, company_id: int, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [r["person_id"] for r in results]
    if not ids:
        return results
    reveals = {
        r.person_id: r
        for r in db.scalars(
            select(ExplorarReveal).where(ExplorarReveal.company_id == company_id, ExplorarReveal.person_id.in_(ids))
        ).all()
    }
    nexus = nexus_revealed_by_person_id(db, [i for i in ids if i not in reveals])
    out = []
    for r in results:
        item = dict(r)
        rev = reveals.get(r["person_id"])
        shared = nexus.get(r["person_id"]) if rev is None else None
        item["revealed"] = rev is not None or shared is not None
        item["revealed_by_nexus"] = shared is not None
        item["email"] = rev.email if rev else (shared or {}).get("email")
        item["phone"] = rev.phone if rev else (shared or {}).get("phone")
        item["prospect_id"] = rev.prospect_id if rev else None
        item["campaign_id"] = rev.campaign_id if rev else None
        # Si ya hay valor, el chip no puede decir No.
        out.append(sync_contact_flags(item))
    return out


def search_plan(filters: dict[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    """Tandas en orden de calidad de contacto: (filtros, cuenta_para_el_total).

    Llena la 1a hoja con mail+cel -> un canal -> sin contacto (solo LinkedIn).
    El total contable es el universo ICP sin exigir mail/cel (ej. ~300 founders
    restaurantes CL, no solo los ~14 con mail).
    El numero NO se revela aca: solo has_mobile; el valor sale con Revelar.
    """
    details = filters.get("person_contact_details") or {}

    def base_filters() -> dict[str, Any]:
        return {k: v for k, v in filters.items() if k != "person_contact_details"}

    def with_contact(base: dict[str, Any], **kw: list[str]) -> dict[str, Any]:
        return {
            **base,
            "person_contact_details": {
                "operator": "AND",
                "hide_people_with_details_already_revealed": False,
                **kw,
            },
        }

    root = base_filters()
    both = with_contact(root, email=["VERIFIED"], mobile=["VERIFIED"])
    mobile_pref = with_contact(root, mobile=["VERIFIED"])
    email_pref = with_contact(root, email=["VERIFIED"])

    # Busqueda por nombre: pocos resultados; priorizar completa y contar el pedido.
    if filters.get("person_name"):
        return [(both, False), (filters, True)]

    # Completos -> un canal -> todos los del ICP (aunque solo tengan LI).
    # Total contable = tanda sin contacto (universo real).
    if details.get("email") and details.get("mobile"):
        return [(both, False), (mobile_pref, False), (email_pref, False), (root, True)]
    if details.get("email") and not details.get("mobile"):
        return [(both, False), (email_pref, False), (mobile_pref, False), (root, True)]
    if details.get("mobile") and not details.get("email"):
        return [(both, False), (mobile_pref, False), (email_pref, False), (root, True)]

    return [
        (both, False),
        (mobile_pref, False),
        (email_pref, False),
        (root, True),
    ]


def total_scope_for_count(filters: dict[str, Any], scopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Scope para el total ICP en Prospeo (sin exigir mail/cel).

    - Con **depto pedido** (ej. RRHH): el pozo debe incluir depto en Prospeo. Si usamos el
      scope sin depto (solo país+industria+nivel), Argentina Manager explota ~50k mientras
      la grilla post-filtrada es RRHH — Chile founders sin depto sigue siendo el caso válido.
    - Con industria sin depto obligatorio: el scope más ancho que conserve industria.
    """
    if not scopes:
        return core_filters_for_prospeo(filters)
    wants_dept = bool(filters.get("person_department"))
    sen = filters.get("person_seniority")
    sen_labels = {
        str(s).strip()
        for s in ((sen or {}).get("include") or [])
        if isinstance(sen, dict) and str(s).strip()
    }
    founder_style = bool(sen_labels & {"Founder/Owner", "C-Suite"})

    if filters.get("company_industry"):
        with_ind = [s for s in scopes if "company_industry" in s]
        if with_ind:
            if wants_dept and not founder_style:
                for s in with_ind:
                    if "person_department" in s:
                        return s
            return with_ind[-1]
    if filters.get("person_seniority"):
        with_sen = [s for s in scopes if "person_seniority" in s]
        if with_sen:
            return with_sen[-1]
    return scopes[-1]


def empty_search_hint(
    filters: dict[str, Any],
    *,
    raw_seen: int,
    prospeo_total: int,
    kept_n: int,
) -> str | None:
    """Mensaje claro si la grilla queda en 0 (pozo vacío vs ICP que tumba todo)."""
    if kept_n > 0:
        return None
    labels: list[str] = []
    if filters.get("person_location_search"):
        labels.append("país")
    if filters.get("company_industry"):
        labels.append("industria")
    if filters.get("person_department"):
        labels.append("área")
    if filters.get("person_seniority"):
        labels.append("nivel")
    if filters.get("company_headcount_range"):
        labels.append("tamaño")
    if filters.get("person_job_title"):
        labels.append("cargo")
    if not labels:
        labels.append("filtros")
    if len(labels) == 1:
        loosen = labels[0]
    elif len(labels) == 2:
        loosen = f"{labels[0]} o {labels[1]}"
    else:
        loosen = ", ".join(labels[:-1]) + f" o {labels[-1]}"
    if raw_seen <= 0 and prospeo_total <= 0:
        return (
            f"Pozo vacío en Prospeo con esta combinación. "
            f"Probá aflojar {loosen}."
        )
    return (
        f"Hubo perfiles en la base, pero ninguno pasó el ICP "
        f"(país, industria, área, tamaño o nivel). Probá aflojar {loosen}."
    )


def prospeo_coverage_note(*, estimated_total: int, exhausted: bool) -> str:
    """Techo explícito: Search no promete cobertura tipo Lusha."""
    base = (
        "Volumen = gente indexada en Prospeo (LinkedIn), no una base tipo Lusha. "
        "Si el número te parece bajo, suele ser el techo de datos, no un bug de Nexus."
    )
    if exhausted and estimated_total > 0:
        return (
            f"{base} Con estos filtros el pozo es "
            f"{int(estimated_total):,}.".replace(",", ".")
        )
    return base


def search_phases(filters: dict[str, Any]) -> list[dict[str, Any]]:
    return [phase for phase, _counted in search_plan(filters)]


def _explorar_region_label(filters: dict[str, Any]) -> str | None:
    loc = filters.get("person_location_search")
    if not isinstance(loc, dict):
        return None
    include = [str(x).strip() for x in (loc.get("include") or []) if str(x).strip()]
    if not include:
        return None
    return ", ".join(include[:12])


def _explorar_geo(hit: dict[str, Any], filters: dict[str, Any]):
    return assess_person_geo(
        campaign_region=_explorar_region_label(filters),
        person_country=hit.get("country"),
        person_location=hit.get("city"),
        company_country=hit.get("company_country"),
        phone=hit.get("phone"),
    )


def annotate_explorar_geo(hit: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    """Adjunta geo_status sin pisar país de persona con el de la empresa."""
    geo = _explorar_geo(hit, filters)
    out = dict(hit)
    out["geo_status"] = geo.status
    out["geo_reason"] = geo.reason
    if geo.company_country and not out.get("company_country"):
        out["company_country"] = geo.company_country
    if geo.status in {"incomplete", "review"} and not geo.resolved_country:
        # No presentar el país de empresa como si fuera el de la persona.
        pass
    elif geo.resolved_country:
        out["country"] = geo.resolved_country
    return out


def store_in_contact_cache(
    db: Session,
    *,
    company_id: int,
    hits: list[dict[str, Any]],
    record_delivery: bool,
) -> int:
    """Guarda personas de Prospeo en la base propia (best-effort, nunca rompe la búsqueda/revelado)."""
    from app.services.nexus_contact_cache import upsert_contact_from_import

    saved = 0
    for hit in hits:
        origin_id = _str(hit.get("source_person_id") or hit.get("person_id")) or None
        try:
            with db.begin_nested():
                row = upsert_contact_from_import(
                    db,
                    tenant_company_id=int(company_id),
                    campaign_id=_int_or_none(hit.get("campaign_id")),
                    name=_str(hit.get("name")),
                    role=_str(hit.get("role")),
                    industry=_str(hit.get("industry")),
                    industry_origin=_str(hit.get("industry_origin")) or "prospeo",
                    country=_str(hit.get("country")),
                    country_origin="person" if _str(hit.get("country")) else None,
                    company_country=_str(hit.get("company_country")),
                    email=_str(hit.get("email")),
                    linkedin_url=_str(hit.get("linkedin_url")),
                    phone=_str(hit.get("phone")),
                    whatsapp=_str(hit.get("phone")),
                    company_name=_str(hit.get("company_name")),
                    company_domain=_str(hit.get("company_domain")),
                    company_size=_str(hit.get("company_size")),
                    size_origin=_str(hit.get("size_origin")) or "prospeo",
                    city=_str(hit.get("city")),
                    source_provider="prospeo",
                    source_external_id=origin_id,
                    source_person_id=origin_id,
                    record_delivery=record_delivery,
                )
            if row is not None:
                saved += 1
        except Exception:  # noqa: BLE001
            continue
    return saved


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def sync_contact_flags(hit: dict[str, Any]) -> dict[str, Any]:
    """Chips Email/Celular fieles: verified o valor revelado ⇒ has_* = Sí."""
    row = dict(hit)
    if row.get("email_verified"):
        row["has_email"] = True
    if row.get("mobile_verified"):
        row["has_mobile"] = True
    if _str(row.get("email")):
        row["has_email"] = True
    if _str(row.get("phone")):
        row["has_mobile"] = True
    return row


def contact_quality_tier(hit: dict[str, Any]) -> int:
    """0 = mejor (mail+cel) … 4 = sin señal. Celular pesa igual o más que mail."""
    hit = sync_contact_flags(hit)
    ev = bool(hit.get("email_verified"))
    mv = bool(hit.get("mobile_verified"))
    he = bool(hit.get("has_email"))
    hm = bool(hit.get("has_mobile"))
    if (ev or he) and (mv or hm):
        return 0 if (ev and mv) else 1
    if mv or hm:
        return 2
    if ev or he:
        return 3
    return 4


def hit_has_complete_contact(hit: dict[str, Any]) -> bool:
    """Mail + celular disponibles (aún sin revelar valores)."""
    hit = sync_contact_flags(hit)
    return bool(hit.get("has_email")) and bool(hit.get("has_mobile"))


def _icp_rank_tuple(hit: dict[str, Any]) -> tuple[int, int, int]:
    """Mejor ICP primero: industria exacta → seniority exacta → chips blandos."""
    ind = str(hit.get("industry_status") or "")
    sen_ok = hit.get("seniority_match")
    if sen_ok is None and hit.get("seniority") is not None:
        # annotate_filter_fit no setea seniority_match; usar filter soft score.
        sen_ok = None
    return (
        -int(_FIRMO_RANK.get(ind, 0)),
        0 if sen_ok is True else (1 if sen_ok is None else 2),
        -int(hit.get("filter_matched") or 0),
    )


def rank_by_contact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1ª hoja: mail+cel → un canal → solo LI. A igual contacto, mejor fit ICP.

    El post-filtro ya exige ICP (industria contradictoria = fuera); acá ordenamos
    para que el pantallazo inicial sea el más completo de contacto.
    """

    def key(r: dict[str, Any]) -> tuple:
        return (contact_quality_tier(r),) + _icp_rank_tuple(r)

    return sorted(rows, key=key)


def _hits_from_linkedin_urls(urls: list[str]) -> list[dict[str, Any]]:
    """Resuelve /in/… vía enrich-person (search-person no indexa a todo LinkedIn por nombre)."""
    from app.services.lead_sourcing.providers.prospeo_mvp import enrich_person_record

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in urls:
        try:
            person = enrich_person_record(
                first_name=None,
                last_name=None,
                full_name=None,
                company_name=None,
                company_website=None,
                linkedin_url=url,
                enrich_mobile=False,
            )
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(person, dict) or not person:
            continue
        hit = parse_hit({"person": person, "company": person.get("company") if isinstance(person.get("company"), dict) else {}})
        if not hit:
            continue
        pid = str(hit.get("person_id") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        if not hit.get("linkedin_url"):
            hit["linkedin_url"] = url
        out.append(hit)
    return out


def search(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    filters: dict[str, Any],
    page: int = 1,
) -> dict[str, Any]:
    if not has_targeting(filters) and not filters.get("_linkedin_profile_urls"):
        raise ExplorarError(
            "Elegí al menos un filtro: nombre, cargo, región, industria, empresa, empresas parecidas o tamaño."
        )
    page = max(1, min(int(page or 1), 1000))

    # La página que ve el usuario se arma recorriendo las páginas de Prospeo en orden y
    # quedándose solo con resultados válidos (cargo exacto, sin gente ya usada en campañas).
    # Se cobra por página que Prospeo nos factura (no por página vista): cada
    # ``pages_per_credit()`` páginas pedidas = 1 crédito, así mirar nunca cuesta más que lo cobrado.
    usage = get_usage(db, company_id)
    per_credit = pages_per_credit()
    start = (page - 1) * PROSPEO_PAGE_SIZE
    wanted = start + PROSPEO_PAGE_SIZE + 1
    budget: dict[str, Any] = {"fetches": 0, "stop": None}
    billing = {"charged": False, "fetched": False}
    # Bajo el timeout HTTP: devolver lo hallado + more_pending en vez de 504.
    deadline_mono = time.monotonic() + search_deadline_sec()
    _deadline_token = _search_deadline_mono.set(deadline_mono)
    try:
        return _search_with_deadline(
            db,
            company_id=company_id,
            user_id=user_id,
            filters=filters,
            page=page,
            usage=usage,
            per_credit=per_credit,
            start=start,
            wanted=wanted,
            budget=budget,
            billing=billing,
            deadline_mono=deadline_mono,
        )
    finally:
        _search_deadline_mono.reset(_deadline_token)


def _search_with_deadline(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    filters: dict[str, Any],
    page: int,
    usage: Any,
    per_credit: int,
    start: int,
    wanted: int,
    budget: dict[str, Any],
    billing: dict[str, Any],
    deadline_mono: float,
) -> dict[str, Any]:
    # Link de perfil LinkedIn en el campo nombre → enrich directo.
    li_urls = [str(u).strip() for u in (filters.get("_linkedin_profile_urls") or []) if str(u).strip()]
    if li_urls and is_name_primary_search(filters) and page == 1:
        li_hits = _hits_from_linkedin_urls(li_urls)
        if li_hits:
            usage.pages_today = int(usage.pages_today) + 1
            usage.pages_total = int(usage.pages_total) + 1
            usage.pages_unbilled = int(usage.pages_unbilled) + 1
            billing["fetched"] = True
            if int(usage.pages_unbilled) >= per_credit:
                try:
                    consume_explorar_credit(
                        db,
                        company_id,
                        user_id,
                        kind="explorar_search",
                        note=f"Explorar: {per_credit} páginas de búsqueda",
                    )
                    usage.pages_unbilled = int(usage.pages_unbilled) - per_credit
                    billing["charged"] = True
                except CreditError:
                    pass
            store_in_contact_cache(db, company_id=company_id, hits=li_hits, record_delivery=False)
            from app.services.prospect_in_use import build_in_use_index

            ranked = rank_by_contact(
                [
                    annotate_filter_fit(
                        annotate_explorar_firmographics(annotate_explorar_geo(r, filters), filters),
                        filters,
                    )
                    for r in li_hits
                ]
            )
            kept_li = apply_in_use(ranked, build_in_use_index(db, company_id), keep_blocked=True)
            db.flush()
            return {
                "page": 1,
                "page_size": PROSPEO_PAGE_SIZE,
                "total_count": len(kept_li),
                "total_is_estimate": False,
                "total_pages": 1 if kept_li else 0,
                "more_pending": False,
                "results": _attach_reveals(db, company_id, kept_li[:PROSPEO_PAGE_SIZE]),
                "charged_credit": billing["charged"],
                "from_cache": False,
                "usage": usage_summary(db, company_id, user_id),
                "name_search": True,
            }

    def load(phase_filters: dict[str, Any], prospeo_page: int) -> dict[str, Any] | None:
        """Página de Prospeo (cache o fetch). None = sin presupuesto/tiempo o sin créditos para pedir más ahora."""
        key = _cache_key(company_id, phase_filters, prospeo_page)
        data = _read_cache(db, key)
        if data is not None:
            return data
        # Margen para armar la respuesta HTTP sin que el middleware corte con 504.
        if time.monotonic() >= deadline_mono - 1.25:
            budget["stop"] = "time"
            return None
        fetch_cap = max_fetches_per_request(filters)
        if budget["fetches"] >= fetch_cap:
            budget["stop"] = "budget"
            return None
        if int(usage.pages_unbilled) + 1 >= per_credit and get_user_available_credits(db, company_id, user_id) < 1:
            if len(kept) > start:
                budget["stop"] = "credits"
                return None
            raise ExplorarError(
                "No tenés créditos para seguir buscando. Pedí créditos a tu director.",
                status_code=402,
            )
        try:
            data = _fetch_page(phase_filters, prospeo_page)
        except ExplorarError as e:
            # Con resultados suficientes para mostrar, mejor una página corta que un error.
            if e.status_code == 429 and len(kept) > start:
                budget["stop"] = "rate"
                return None
            raise
        budget["fetches"] += 1
        billing["fetched"] = True
        usage.pages_today = int(usage.pages_today) + 1
        usage.pages_total = int(usage.pages_total) + 1
        n_hits = len(data.get("results") or [])
        if n_hits == 0:
            _logger.warning(
                "[explorar] Prospeo vacío company=%s page=%s filters_keys=%s",
                company_id,
                prospeo_page,
                sorted(phase_filters.keys()),
            )
        if data["results"]:
            store_in_contact_cache(db, company_id=company_id, hits=data["results"], record_delivery=False)
            _write_cache(db, company_id, key, data)
            usage.pages_unbilled = int(usage.pages_unbilled) + 1
            if int(usage.pages_unbilled) >= per_credit:
                try:
                    consume_explorar_credit(
                        db,
                        company_id,
                        user_id,
                        kind="explorar_search",
                        note=f"Explorar: {per_credit} páginas de búsqueda",
                    )
                    usage.pages_unbilled = int(usage.pages_unbilled) - per_credit
                    billing["charged"] = True
                except CreditError:
                    pass
        return data

    from app.services.prospect_in_use import build_in_use_index

    fetch_limit = max_fetches_per_request(filters)
    # Scopes: estricto → sin industria en Prospeo (volumen). Contact phases por scope.
    scopes = volume_scope_filters(filters)
    query_filters = scopes[0]
    # Total = ICP usable más ancho (sigue industria si la pediste), sin exigir contacto.
    wide_for_total = total_scope_for_count(filters, scopes)
    plan_for_total = search_plan(wide_for_total)
    favorite_ids = favorite_person_ids(db, user_id)
    in_use = build_in_use_index(db, company_id)
    name_search = bool(filters.get("person_name"))
    # v2: total = ICP sin exigir mail/cel (v1 cacheaba ~docenas del pozo mail).
    total_key = _cache_key(company_id, filters, "total_icp_v3")
    phase_totals: dict[int, int] = {}
    kept: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    raw_seen = 0
    exhausted = True
    stopped = False
    global_phase_idx = 0
    counted_global: set[int] = set()
    try:
        total_scope_i = next(i for i, s in enumerate(scopes) if s == wide_for_total)
    except StopIteration:
        total_scope_i = len(scopes) - 1
    for scope_i, scope_filters in enumerate(scopes):
        plan = search_plan(scope_filters)
        # Contable = scope de total_scope_for_count (industria intacta), no el país suelto.
        scope_counted = (
            {idx for idx, (_phase, c) in enumerate(plan) if c} if scope_i == total_scope_i else set()
        )
        scope_kept_before = len(kept)
        scope_fetches_before = int(budget["fetches"])
        for phase_idx, _phase_counted in enumerate(plan):
            phase_filters = plan[phase_idx][0]
            contact = phase_filters.get("person_contact_details")
            phase_filters = core_filters_for_prospeo(
                {k: v for k, v in phase_filters.items() if k != "person_contact_details"}
            )
            if contact:
                phase_filters["person_contact_details"] = contact
            if phase_idx in scope_counted:
                counted_global.add(global_phase_idx)
            prospeo_page = 1
            phase_raw = 0
            phase_kept_before = len(kept)
            while len(kept) < wanted:
                data = load(phase_filters, prospeo_page)
                if data is None:
                    stopped = True
                    break
                phase_totals[global_phase_idx] = max(
                    phase_totals.get(global_phase_idx, 0), int(data.get("total_count") or 0)
                )
                rows = [r for r in (data.get("results") or []) if str(r.get("person_id")) not in seen_ids]
                if not data.get("results"):
                    break
                phase_details = phase_filters.get("person_contact_details") or {}
                if phase_details.get("email") == ["VERIFIED"] or phase_details.get("mobile") == ["VERIFIED"]:
                    stamped_rows: list[dict[str, Any]] = []
                    for r in rows:
                        row = dict(r)
                        if phase_details.get("email") == ["VERIFIED"]:
                            row["has_email"] = True
                            row["email_verified"] = True
                        if phase_details.get("mobile") == ["VERIFIED"]:
                            row["has_mobile"] = True
                            row["mobile_verified"] = True
                        stamped_rows.append(row)
                    rows = stamped_rows
                seen_ids.update(str(r.get("person_id")) for r in rows)
                raw_seen += len(rows)
                phase_raw += len(rows)
                candidates = [
                    r
                    for r in rows
                    if str(r.get("person_id")) not in favorite_ids and hit_matches_filters(r, filters)
                ]
                if not candidates and rows and start == 0 and len(kept) == 0:
                    candidates = [
                        r
                        for r in rows
                        if str(r.get("person_id")) not in favorite_ids
                        and _hit_usable_without_strict_title(r, filters)
                    ]
                matching = rank_by_contact(
                    [
                        annotate_filter_fit(
                            annotate_explorar_firmographics(annotate_explorar_geo(r, filters), filters),
                            filters,
                        )
                        for r in candidates
                    ]
                )
                kept.extend(apply_in_use(matching, in_use, keep_blocked=name_search))
                total_pages = int(data.get("total_pages") or 0)
                prospeo_page += 1
                if prospeo_page > total_pages:
                    break
                # Keep-rate miserable: no quemar budget; pasar a la siguiente tanda/scope.
                if (
                    scope_i < len(scopes) - 1
                    and prospeo_page > 1
                    and phase_raw >= 25
                    and (len(kept) - phase_kept_before) < max(2, phase_raw // 8)
                ):
                    break
            global_phase_idx += 1
            if stopped or len(kept) >= wanted:
                exhausted = False
                break
        if stopped or len(kept) >= wanted:
            exhausted = False
            break
        # Scope sin avance útil → siguiente con el budget restante.
        if (
            scope_i < len(scopes) - 1
            and (len(kept) - scope_kept_before) < 3
            and (int(budget["fetches"]) - scope_fetches_before) >= 2
        ):
            continue
    # Compat: query_filters / plan_for_total usados más abajo en fallbacks.
    plan = plan_for_total
    phases = []
    counted = set()
    for idx, (phase_f, is_counted) in enumerate(plan_for_total):
        contact = phase_f.get("person_contact_details")
        built = core_filters_for_prospeo(
            {k: v for k, v in phase_f.items() if k != "person_contact_details"}
        )
        if contact:
            built["person_contact_details"] = contact
        phases.append(built)
        if is_counted:
            counted.add(idx)

    # Si Prospeo no trajo nada con el plan (o el post-filtro dejó 0), aflojar ICP blando
    # pero seguir pidiendo contacto (mail+cel → cel → mail). Nunca grilla sin señal de contacto.
    if not kept and start == 0 and budget["fetches"] < fetch_limit:
        fallbacks: list[dict[str, Any]] = []

        def _with_contact_fb(base: dict[str, Any], **kw: list[str]) -> dict[str, Any]:
            return {
                **base,
                "person_contact_details": {
                    "operator": "AND",
                    "hide_people_with_details_already_revealed": False,
                    **kw,
                },
            }

        raw = {k: v for k, v in query_filters.items() if k != "person_contact_details"}
        soft_variants: list[dict[str, Any]] = [raw]
        loose = dict(raw)
        for drop_key in ("company_industry", "person_department", "company_headcount_range", "person_seniority"):
            if drop_key in loose:
                loose = {k: v for k, v in loose.items() if k != drop_key}
                soft_variants.append(loose)
        core_only = core_filters_for_prospeo(
            {k: v for k, v in filters.items() if k != "person_contact_details"}
        )
        if core_only and core_only not in soft_variants:
            soft_variants.append(core_only)
        for base in soft_variants:
            fallbacks.append(_with_contact_fb(base, email=["VERIFIED"], mobile=["VERIFIED"]))
            fallbacks.append(_with_contact_fb(base, mobile=["VERIFIED"]))
            fallbacks.append(_with_contact_fb(base, email=["VERIFIED"]))
        for fb in fallbacks:
            if len(kept) >= wanted or budget["fetches"] >= fetch_limit:
                break
            data = load(fb, 1)
            if not data or not data.get("results"):
                continue
            rows = [r for r in (data.get("results") or []) if str(r.get("person_id")) not in seen_ids]
            seen_ids.update(str(r.get("person_id")) for r in rows)
            raw_seen += len(rows)
            candidates = [
                r
                for r in rows
                if str(r.get("person_id")) not in favorite_ids
                and (
                    hit_matches_filters(r, filters)
                    or _hit_usable_without_strict_title(r, filters)
                )
            ]
            if not candidates and rows:
                candidates = [
                    r
                    for r in rows
                    if str(r.get("person_id")) not in favorite_ids and hit_matches_core_filters(r, filters)
                ]
            matching = rank_by_contact(
                [
                    annotate_filter_fit(
                        annotate_explorar_firmographics(annotate_explorar_geo(r, filters), filters),
                        filters,
                    )
                    for r in candidates
                ]
            )
            # Stamp contacto del fallback (misma regla que tandas normales).
            fb_details = fb.get("person_contact_details") or {}
            if fb_details.get("email") == ["VERIFIED"] or fb_details.get("mobile") == ["VERIFIED"]:
                stamped_m: list[dict[str, Any]] = []
                for r in matching:
                    row = dict(r)
                    if fb_details.get("email") == ["VERIFIED"]:
                        row["has_email"] = True
                        row["email_verified"] = True
                    if fb_details.get("mobile") == ["VERIFIED"]:
                        row["has_mobile"] = True
                        row["mobile_verified"] = True
                    stamped_m.append(row)
                matching = stamped_m
            kept.extend(apply_in_use(matching, in_use, keep_blocked=name_search))
            # El total contable sigue siendo la tanda de mail cuando exista.
            if counted:
                # no pisar phase_totals de mail con un fallback chico
                pass
            else:
                phase_totals[0] = max(phase_totals.get(0, 0), int(data.get("total_count") or 0))
            if kept:
                exhausted = False
                _logger.info(
                    "[explorar] fallback OK company=%s kept=%s keys=%s",
                    company_id,
                    len(kept),
                    sorted(fb.keys()),
                )
                break

    # Completos primero; un canal; solo LI. Chips fieles al stamp / payload.
    kept = [sync_contact_flags(r) for r in kept]
    kept = rank_by_contact(kept)
    page_results = kept[start : start + PROSPEO_PAGE_SIZE]
    # Total = universo ICP sin exigir contacto (Prospeo). No achicar por keep-rate
    # de la 1ª tanda mail+cel (eso dejó Chile en ~26 con pozo ~300).
    total_count = int((_read_cache(db, total_key) or {}).get("total") or 0)
    if not total_count and counted:
        # Una sola tanda ICP sin contacto (no sumar mail+cel+LI — inflaba el total).
        idx = max(counted)
        if idx < len(phases):
            first = load(phases[idx], 1)
            if first is not None:
                total_count = int(first.get("total_count") or 0)
                if total_count:
                    _write_cache(db, company_id, total_key, {"total": total_count})
    if total_count:
        estimated_total = max(len(kept), total_count)
    elif exhausted:
        estimated_total = len(kept)
    else:
        estimated_total = max(len(kept), len(kept) + (1 if stopped else 0))
    if stopped and len(page_results) < PROSPEO_PAGE_SIZE and estimated_total <= len(kept):
        estimated_total = max(estimated_total, len(kept) + 1)
    virtual_pages = -(-estimated_total // PROSPEO_PAGE_SIZE) if estimated_total else 0
    if len(kept) > start + PROSPEO_PAGE_SIZE:
        virtual_pages = max(virtual_pages, page + 1)
    elif page_results:
        virtual_pages = max(virtual_pages, page)
    # Universo ICP > lo cargado en este request → al menos 2 páginas visibles.
    if estimated_total > len(page_results) and virtual_pages < 2:
        virtual_pages = 2
    if estimated_total > PROSPEO_PAGE_SIZE:
        virtual_pages = max(virtual_pages, -(-estimated_total // PROSPEO_PAGE_SIZE))

    db.flush()

    prospeo_pool = max(int(total_count or 0), max(phase_totals.values()) if phase_totals else 0)
    hint = empty_search_hint(
        filters,
        raw_seen=raw_seen,
        prospeo_total=prospeo_pool,
        kept_n=len(kept),
    )

    return {
        "page": page,
        "page_size": PROSPEO_PAGE_SIZE,
        "total_count": estimated_total,
        "total_is_estimate": not exhausted,
        "total_pages": virtual_pages,
        # Página corta por tope de fetches, rate limit o wall-clock: el front reintenta y avanza (cache).
        "more_pending": bool(
            stopped
            and budget["stop"] in ("budget", "rate", "time")
            and len(page_results) < PROSPEO_PAGE_SIZE
        ),
        "empty_hint": hint,
        "coverage_note": prospeo_coverage_note(
            estimated_total=int(estimated_total or 0),
            exhausted=bool(exhausted),
        ),
        "results": _attach_reveals(db, company_id, page_results),
        "charged_credit": billing["charged"],
        "from_cache": not billing["fetched"],
        "usage": usage_summary(db, company_id, user_id),
    }


def get_reveal(db: Session, company_id: int, person_id: str) -> ExplorarReveal | None:
    return db.scalars(
        select(ExplorarReveal).where(
            ExplorarReveal.company_id == company_id,
            ExplorarReveal.person_id == str(person_id),
        )
    ).first()


@dataclass(frozen=True)
class KnownContact:
    """Datos que Nexus ya pagó alguna vez (cualquier cliente). `complete`: no hay nada más que buscar."""

    email: str | None
    phone: str | None
    linkedin_url: str | None
    name: str | None
    role: str | None
    complete: bool


def _nexus_known_contact(db: Session, person_id: str, snapshot: dict[str, Any]) -> KnownContact | None:
    """
    Revelados de Nexus, para todos los clientes: si cualquier empresa ya reveló a esta persona
    (o Nexus ya pagó su mail y celular al armar una campaña), no se le cobra a nadie más.
    """
    prior = db.scalars(
        select(ExplorarReveal)
        .where(
            ExplorarReveal.person_id == str(person_id),
            or_(ExplorarReveal.email.is_not(None), ExplorarReveal.phone.is_not(None)),
        )
        .order_by(ExplorarReveal.id.asc())
        .limit(1)
    ).first()
    if prior is not None:
        return KnownContact(
            email=prior.email,
            phone=prior.phone,
            linkedin_url=prior.linkedin_url,
            name=prior.name,
            role=prior.role,
            complete=True,
        )
    try:
        from app.services.nexus_contact_cache import lookup_revealed_contact

        hit = lookup_revealed_contact(
            db,
            linkedin_url=_str(snapshot.get("linkedin_url")),
            source_provider="prospeo",
            source_external_id=person_id,
            source_person_id=person_id,
        )
    except Exception:  # noqa: BLE001
        return None
    if hit is None:
        return None
    email = _str(hit.email)
    phone = _str(hit.whatsapp or hit.phone)
    if not email and not phone:
        return None
    return KnownContact(
        email=email,
        phone=phone,
        linkedin_url=_str(hit.linkedin_url),
        name=_str(hit.full_name),
        role=_str(hit.role),
        complete=bool(email and phone),
    )


def nexus_revealed_by_person_id(db: Session, person_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Personas ya reveladas por algún cliente de Nexus: se muestran reveladas a todos, gratis."""
    ids = [str(p) for p in person_ids if p]
    if not ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in db.scalars(
        select(ExplorarReveal)
        .where(
            ExplorarReveal.person_id.in_(ids),
            or_(ExplorarReveal.email.is_not(None), ExplorarReveal.phone.is_not(None)),
        )
        .order_by(ExplorarReveal.id.asc())
    ).all():
        out.setdefault(r.person_id, {"email": r.email, "phone": r.phone})
    missing = [i for i in ids if i not in out]
    if missing:
        from app.models.nexus_contact_cache import NexusContactCache

        has_source_person = hasattr(NexusContactCache, "source_person_id")
        identity_clause = (
            or_(
                NexusContactCache.source_person_id.in_(missing),
                NexusContactCache.source_external_id.in_(missing),
            )
            if has_source_person
            else NexusContactCache.source_external_id.in_(missing)
        )
        for c in db.scalars(
            select(NexusContactCache).where(
                identity_clause,
                NexusContactCache.email.is_not(None),
                or_(NexusContactCache.whatsapp.is_not(None), NexusContactCache.phone.is_not(None)),
            )
        ).all():
            key = str(
                (getattr(c, "source_person_id", None) if has_source_person else None)
                or c.source_external_id
                or ""
            )
            if key:
                out.setdefault(key, {"email": c.email, "phone": c.whatsapp or c.phone})
    return out


def reveal_person(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    snapshot: dict[str, Any],
    charge: bool = True,
    ensure_base: bool = True,
) -> tuple[ExplorarReveal, bool]:
    """
    Devuelve (reveal, cobrado_ahora). No cobra si ya estaba revelado en la empresa ni si otro
    cliente de Nexus ya lo reveló (los datos quedan revelados para todos).

    `ensure_base=False` es solo para el alta a campaña: el revelado intermedio sin cobro
    no entra a la base hasta que el prospecto quede en la campaña (si falla el alta, se borra).
    """
    person_id = str(snapshot.get("person_id") or "").strip()
    if not person_id:
        raise ExplorarError("Falta el identificador de la persona.")
    existing = get_reveal(db, company_id, person_id)
    if existing is not None:
        # Revelados viejos sin prospecto: subirlos a la base.
        if ensure_base:
            ensure_prospect_from_reveal(
                db,
                company_id=company_id,
                user_id=user_id,
                reveal=existing,
                snapshot=snapshot,
            )
        return existing, False

    from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
    from app.services.lead_sourcing.providers.prospeo_mvp import enrich_person_by_id, extract_email_phone

    known = _nexus_known_contact(db, person_id, snapshot)
    has_credit = (not charge) or get_user_available_credits(db, company_id, user_id) >= 1
    person: dict[str, Any] | None = None
    if known is not None and (known.complete or not has_credit):
        email, phone = known.email, known.phone
        charge = False
    else:
        if not has_credit:
            raise ExplorarError(
                "No tenés créditos para revelar contactos. Pedí créditos a tu director.", status_code=402
            )
        try:
            person = enrich_person_by_id(person_id, enrich_mobile=True)
            email, phone = extract_email_phone(person or {})
        except ProviderNotConfiguredError as e:
            if known is None:
                raise ExplorarError(str(e), status_code=503) from e
            email, phone = None, None
        except ProviderAPIError as e:
            if known is None:
                raise ExplorarError("No pudimos revelar este contacto. Probá de nuevo.", status_code=502) from e
            email, phone = None, None
        if known is not None:
            # Solo se cobra si Prospeo trajo algo que Nexus todavía no tenía.
            gained = bool((email and not known.email) or (phone and not known.phone))
            email, phone = email or known.email, phone or known.phone
            charge = charge and gained
    linkedin = (
        _str((person or {}).get("linkedin_url"))
        or (known.linkedin_url if known else None)
        or _str(snapshot.get("linkedin_url"))
    )
    if not email and not phone:
        raise ExplorarError(
            "Prospeo no tiene email ni celular para esta persona. No se descontó ningún crédito.",
            status_code=404,
        )

    if charge:
        try:
            consume_explorar_credit(
                db,
                company_id,
                user_id,
                kind="explorar_reveal",
                note=f"Explorar: revelado {str(snapshot.get('name') or person_id)[:80]}",
            )
        except CreditError as e:
            raise ExplorarError(str(e), status_code=402) from e

    def _snap(key: str, limit: int) -> str | None:
        v = _str(snapshot.get(key))
        return v[:limit] if v else None

    row = ExplorarReveal(
        company_id=company_id,
        person_id=person_id[:64],
        revealed_by_user_id=user_id,
        charged=1 if charge else 0,
        name=(
            _str((person or {}).get("full_name")) or _snap("name", 255) or (known.name if known else None) or "—"
        )[:255],
        first_name=_snap("first_name", 128),
        last_name=_snap("last_name", 128),
        role=(
            _str((person or {}).get("current_job_title"))
            or _snap("role", 255)
            or (known.role if known else None)
            or None
        ),
        company_name=_snap("company_name", 255),
        company_domain=_snap("company_domain", 255),
        country=_snap("country", 128),
        industry=_snap("industry", 128),
        linkedin_url=linkedin[:512] if linkedin else None,
        email=email,
        phone=phone,
    )
    if row.role:
        row.role = row.role[:255]
    db.add(row)
    db.flush()
    store_in_contact_cache(
        db,
        company_id=company_id,
        hits=[{**snapshot, **reveal_to_dict(row), "person_id": person_id, "campaign_id": None}],
        record_delivery=True,
    )
    # Queda en la base de prospectos (contenedor individual), no solo en explorar_reveals.
    if ensure_base:
        ensure_prospect_from_reveal(
            db,
            company_id=company_id,
            user_id=user_id,
            reveal=row,
            snapshot=snapshot,
        )
    return row, charge


def favorite_person_ids(db: Session, user_id: int) -> set[str]:
    return {
        str(pid)
        for pid in db.scalars(select(ExplorarFavorite.person_id).where(ExplorarFavorite.user_id == int(user_id))).all()
    }


def add_favorite(db: Session, *, company_id: int, user_id: int, snapshot: dict[str, Any]) -> ExplorarFavorite:
    person_id = str(snapshot.get("person_id") or "").strip()[:64]
    if not person_id:
        raise ExplorarError("Falta el identificador de la persona.")
    row = db.scalars(
        select(ExplorarFavorite).where(ExplorarFavorite.user_id == int(user_id), ExplorarFavorite.person_id == person_id)
    ).first()
    if row is None:
        row = ExplorarFavorite(company_id=int(company_id), user_id=int(user_id), person_id=person_id)
        db.add(row)

    def _snap(key: str, limit: int) -> str | None:
        v = _str(snapshot.get(key))
        return v[:limit] if v else None

    row.name = (_snap("name", 255) or "—")
    row.first_name = _snap("first_name", 128)
    row.last_name = _snap("last_name", 128)
    row.role = _snap("role", 255)
    row.company_name = _snap("company_name", 255)
    row.company_domain = _snap("company_domain", 255)
    row.company_size = _snap("company_size", 64)
    row.country = _snap("country", 128)
    row.city = _snap("city", 128)
    row.industry = _snap("industry", 128)
    row.linkedin_url = _snap("linkedin_url", 512)
    for flag in ("has_email", "has_mobile", "email_verified", "mobile_verified"):
        setattr(row, flag, 1 if snapshot.get(flag) else 0)
    db.flush()
    return row


def remove_favorite(db: Session, *, company_id: int, person_id: str, user_id: int | None = None) -> int:
    """Saca la estrella. Sin `user_id` la saca para toda la empresa (pasó a una campaña)."""
    stmt = delete(ExplorarFavorite).where(
        ExplorarFavorite.company_id == int(company_id),
        ExplorarFavorite.person_id == str(person_id),
    )
    if user_id is not None:
        stmt = stmt.where(ExplorarFavorite.user_id == int(user_id))
    res = db.execute(stmt)
    db.flush()
    return int(res.rowcount or 0)


def list_favorites(db: Session, *, company_id: int, user_id: int, include_team: bool) -> list[dict[str, Any]]:
    from app.models.user import User

    stmt = select(ExplorarFavorite).where(ExplorarFavorite.company_id == int(company_id))
    if not include_team:
        stmt = stmt.where(ExplorarFavorite.user_id == int(user_id))
    rows = db.scalars(stmt.order_by(ExplorarFavorite.id.desc())).all()
    owners = {
        u.id: (u.name or u.email or "").strip()
        for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows} or {0}))).all()
    }
    items = [
        {
            "person_id": r.person_id,
            "source_person_id": r.person_id,
            "source_provider": "prospeo",
            "name": r.name,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "role": r.role,
            "company_name": r.company_name,
            "company_domain": r.company_domain,
            "company_size": r.company_size,
            "country": r.country,
            "city": r.city,
            "industry": r.industry,
            "linkedin_url": r.linkedin_url,
            "has_email": bool(r.has_email),
            "has_mobile": bool(r.has_mobile),
            "email_verified": bool(r.email_verified),
            "mobile_verified": bool(r.mobile_verified),
            "favorite": True,
            "owner_user_id": r.user_id,
            "owner_name": owners.get(r.user_id) or "",
            "mine": int(r.user_id) == int(user_id),
            "saved_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    from app.services.prospect_in_use import build_in_use_index

    in_use = build_in_use_index(db, company_id)
    for item in items:
        block = in_use.blocking(item)
        if block is not None:
            item["in_use"] = block
    return _attach_reveals(db, company_id, items)


def ensure_prospect_from_reveal(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    reveal: ExplorarReveal,
    snapshot: dict[str, Any] | None = None,
) -> Any:
    """Deja el revelado en la base de prospectos (Secuencias individuales).

    No arranca secuencia. Si ya existe el contacto en la empresa, solo enlaza el reveal.
    """
    from app.models.product import Product
    from app.models.prospect import Prospect
    from app.models.user import User
    from app.routes.prospects import _persist_new_prospect
    from app.schemas.prospect import ProspectCreate
    from app.services.manual_sequence_kickoff import (
        find_sequence_operator,
        get_or_create_individual_container,
        is_individual_container_campaign,
    )

    snap = snapshot if isinstance(snapshot, dict) else {}
    if reveal.prospect_id:
        existing = db.get(Prospect, int(reveal.prospect_id))
        if existing is not None and int(existing.company_id) == int(company_id):
            if reveal.email and not existing.email:
                existing.email = reveal.email
            if reveal.phone and not existing.phone:
                existing.phone = reveal.phone
                if not existing.whatsapp:
                    existing.whatsapp = reveal.phone
            if reveal.campaign_id is None:
                reveal.campaign_id = int(existing.campaign_id)
            db.flush()
            return existing

    found = find_used_prospect(
        db,
        company_id=company_id,
        person_id=reveal.person_id,
        linkedin_url=reveal.linkedin_url or snap.get("linkedin_url"),
        email=reveal.email,
        phone=reveal.phone,
    )
    if found is not None:
        reveal.prospect_id = int(found.id)
        reveal.campaign_id = int(found.campaign_id)
        if reveal.email and not found.email:
            found.email = reveal.email
        if reveal.phone and not found.phone:
            found.phone = reveal.phone
            if not found.whatsapp:
                found.whatsapp = reveal.phone
        db.flush()
        return found

    product = None
    if snap.get("product_id"):
        product = db.get(Product, int(snap["product_id"]))
    if product is None or int(product.company_id) != int(company_id):
        product = db.scalars(
            select(Product)
            .where(Product.company_id == int(company_id), Product.is_active.is_(True))
            .order_by(Product.id.asc())
        ).first()
    if product is None:
        product = db.scalars(
            select(Product).where(Product.company_id == int(company_id)).order_by(Product.id.asc())
        ).first()
    if product is None:
        _logger.warning(
            "[explorar] reveal sin producto company=%s person=%s — no se creó prospecto",
            company_id,
            reveal.person_id,
        )
        return None

    actor = db.get(User, int(user_id))
    operator = find_sequence_operator(db, company_id=int(company_id), preferred=actor)
    container = get_or_create_individual_container(
        db,
        company_id=int(company_id),
        product=product,
        operator=operator,
    )
    if not is_individual_container_campaign(container):
        return None

    src_provider, src_person_id, src_external_id = explorar_source_identity(
        {**snap, "person_id": reveal.person_id, "source_person_id": reveal.person_id}
    )
    domain = _str(reveal.company_domain) or _str(snap.get("company_domain"))
    create = ProspectCreate(
        name=(reveal.name or snap.get("name") or "—")[:255] or "—",
        company_name=(reveal.company_name or snap.get("company_name") or "—")[:255] or "—",
        role=reveal.role or snap.get("role"),
        industry=reveal.industry or snap.get("industry"),
        industry_origin=snap.get("industry_origin") or "prospeo",
        company_size=snap.get("company_size"),
        size_origin=snap.get("size_origin"),
        country=reveal.country or snap.get("country"),
        city=snap.get("city"),
        linkedin_url=reveal.linkedin_url or snap.get("linkedin_url"),
        email=reveal.email,
        phone=reveal.phone,
        whatsapp=reveal.phone,
        company_website=f"https://{domain}" if domain else None,
        company_domain=domain,
        source_provider=src_provider or "prospeo",
        source_external_id=src_external_id,
        source_person_id=src_person_id or reveal.person_id,
        notes="Revelado desde Explorar",
    )
    row = _persist_new_prospect(db, container, create, released_ok=True)
    reveal.prospect_id = int(row.id)
    reveal.campaign_id = int(container.id)
    db.flush()
    return row


def reveal_to_dict(row: ExplorarReveal) -> dict[str, Any]:
    return {
        "person_id": row.person_id,
        "source_person_id": row.person_id,
        "source_provider": "prospeo",
        "name": row.name,
        "role": row.role,
        "company_name": row.company_name,
        "company_domain": row.company_domain,
        "country": row.country,
        "industry": row.industry,
        "linkedin_url": row.linkedin_url,
        "email": row.email,
        "phone": row.phone,
        "revealed": True,
        "paid": bool(row.charged),
        "prospect_id": row.prospect_id,
        "campaign_id": row.campaign_id,
    }
