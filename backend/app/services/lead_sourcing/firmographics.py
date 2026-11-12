"""Clasificación central de industria y tamaño de empresa.

Equivalente conceptual a ``assess_person_geo``. No confirma una dimensión con la otra.
Fuente única de industria/tamaño para el gate de importación y la reutilización de caché.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from app.services.campaign_icp import is_icp_token_empty

DimStatus = Literal[
    "exact",
    "equivalent",
    "similar",
    "incomplete",
    "contradictory",
    "review",
]
CombinedStatus = Literal[
    "confirmed",
    "similar",
    "incomplete",
    "contradictory",
    "review",
]

# Procedencia persistida. NULL/vacío en filas históricas = uncertain.
INDUSTRY_ORIGINS = ("prospeo", "brave", "icp", "uncertain")
SIZE_ORIGINS = ("prospeo", "brave", "icp", "uncertain")
_OBSERVED_ORIGINS = frozenset({"prospeo", "brave"})
_ORIGIN_TRUST = {"prospeo": 3, "brave": 2, "uncertain": 1, "icp": 0}

_ORIGIN_ALIASES = {
    "prospeo": "prospeo",
    "prospeo_firmo": "prospeo",
    "prospeo_mvp": "prospeo",
    "prospeo_role_first": "prospeo",
    "brave": "brave",
    "web_search": "brave",
    "company_extraction": "brave",
    "icp": "icp",
    "inherited": "icp",
    "campaign": "icp",
    "uncertain": "uncertain",
    "unknown": "uncertain",
    "historical": "uncertain",
}


def _norm(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    return re.sub(
        r"\s+",
        " ",
        "".join(ch for ch in raw if not unicodedata.combining(ch)).lower(),
    ).strip()


def normalize_origin(raw: str | None) -> str | None:
    key = _norm(raw).replace(" ", "_")
    if not key:
        return None
    return _ORIGIN_ALIASES.get(key, key if key in _ORIGIN_TRUST else None)


def origin_trust(raw: str | None) -> int:
    return _ORIGIN_TRUST.get(normalize_origin(raw) or "uncertain", 1)


def is_observed_origin(raw: str | None) -> bool:
    return normalize_origin(raw) in _OBSERVED_ORIGINS


# --- Industria ----------------------------------------------------------------
# Equivalencias conservadoras: un canónico, un cluster. No mezclar producto y servicios IT.

@dataclass(frozen=True)
class IndustryCanon:
    key: str
    cluster: str
    aliases: tuple[str, ...]


_INDUSTRY_CANONS: tuple[IndustryCanon, ...] = (
    IndustryCanon(
        "software_product",
        "tech",
        (
            "saas",
            "software / saas",
            "software development",
            "computer software",
            "b2b saas",
            "technology, information and internet",
            "information technology and services",
        ),
    ),
    IndustryCanon(
        "it_services",
        "tech",
        (
            "it services and it consulting",
            "it services",
            "it / servicios tecnologicos",
            "servicios it",
            "it consulting",
        ),
    ),
    IndustryCanon(
        "veterinary",
        "veterinary",
        (
            "veterinary",
            "veterinary care",
            "veterinary services",
            "animal health",
            "pet care",
            "pet services",
        ),
    ),
    IndustryCanon(
        "healthcare",
        "healthcare",
        (
            "hospitals and health care",
            "medical practices",
            "health care",
            "healthcare",
            "hospital & health care",
        ),
    ),
    IndustryCanon(
        "legal",
        "legal",
        (
            "law practice",
            "legal services",
            "abogados",
            "estudios juridicos",
        ),
    ),
    IndustryCanon(
        "education",
        "education",
        (
            "higher education",
            "education",
            "e-learning",
            "primary and secondary education",
        ),
    ),
    IndustryCanon(
        "real_estate",
        "real_estate",
        (
            "inmobiliaria",
            "real estate",
            "real estate agents and brokers",
        ),
    ),
    IndustryCanon(
        "logistics",
        "logistics",
        (
            "logistica",
            "logistica / transporte",
            "transportation, logistics, supply chain and storage",
            "truck transportation",
            "warehousing and storage",
            "freight and package transportation",
            "supply chain",
        ),
    ),
)

# Señales fuertes en nombre/rol (cuando la etiqueta Prospeo miente o falta).
# Estructura: pedís cluster X → si el texto grita cluster Y ≠ X, fuera.
_SECTOR_TEXT_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "veterinary",
        re.compile(
            r"veterinar\w*|veterinary|"
            r"cl[ií]nica\s+veterinar\w*|hospital\s+veterinar\w*|"
            r"pet\s+(care|hospital|clinic|shop)|"
            r"animal\s+(clinic|hospital|health|care)",
            re.I,
        ),
    ),
    (
        "tech",
        re.compile(
            r"\b(saas|software|fintech|devtools?|cloud\s*ops|"
            r"it\s*consulting|plataforma\s+digital)\b",
            re.I,
        ),
    ),
    (
        "legal",
        re.compile(r"\b(abogad\w*|law\s*firm|estudio\s+jur[ií]dico|legal\s+services)\b", re.I),
    ),
    (
        "healthcare",
        re.compile(
            r"\b(hospital(?!\s+veterinar)|cl[ií]nica\s+m[eé]dica|"
            r"health\s*care|medical\s+center)\b",
            re.I,
        ),
    ),
    (
        "real_estate",
        re.compile(r"\b(inmobiliaria|real\s*estate|proptech)\b", re.I),
    ),
    (
        "education",
        re.compile(r"\b(universidad|university|bootcamp|colegio|e-?learning)\b", re.I),
    ),
    (
        "logistics",
        re.compile(r"\b(log[ií]stica|freight|warehousing|supply\s*chain)\b", re.I),
    ),
)


def icp_industry_clusters(campaign_industry: str | None) -> set[str]:
    """Clusters permitidos por el ICP de industria (varios labels separados por coma)."""
    blob = (campaign_industry or "").strip()
    if not blob or is_icp_token_empty(blob):
        return set()
    out: set[str] = set()
    for part in re.split(r"[,;/|]+", blob):
        p = part.strip()
        if not p:
            continue
        canon = resolve_industry_canon(p)
        if canon:
            out.add(canon.cluster)
            continue
        # Fallback liviano si aún no hay canónico.
        low = _norm(p)
        if any(t in low for t in ("software", "saas", "it services", "technology")):
            out.add("tech")
        elif "veterinar" in low or "animal health" in low or "pet care" in low:
            out.add("veterinary")
        elif "real estate" in low or "inmobili" in low:
            out.add("real_estate")
        elif "legal" in low or "abogad" in low:
            out.add("legal")
        elif "health" in low or "hospital" in low:
            out.add("healthcare")
        elif "educat" in low or "universidad" in low:
            out.add("education")
        elif "logist" in low:
            out.add("logistics")
    return out


def sector_clusters_from_text(
    *,
    company_name: str | None = None,
    role: str | None = None,
    industry: str | None = None,
) -> set[str]:
    """Clusters inferidos de industria etiquetada + señales en nombre/rol."""
    out: set[str] = set()
    ind = (industry or "").strip()
    if ind:
        canon = resolve_industry_canon(ind)
        if canon:
            out.add(canon.cluster)
        else:
            # industria cruda desconocida: igual aporta cluster vía señales de texto
            for cluster, pattern in _SECTOR_TEXT_SIGNALS:
                if pattern.search(ind):
                    out.add(cluster)
    blob = f"{company_name or ''} {role or ''}".strip()
    if blob:
        for cluster, pattern in _SECTOR_TEXT_SIGNALS:
            if pattern.search(blob):
                out.add(cluster)
    return out


def sector_mismatch_reason(
    campaign_industry: str | None,
    *,
    company_name: str | None = None,
    role: str | None = None,
    industry: str | None = None,
) -> str | None:
    """
    Regla estructural: si el ICP pide sector(es) X, no puede haber señal de Y≠X.

    - Pedís software + “Clínica Veterinaria” (aunque Prospeo diga Software) → fuera.
    - Pedís veterinaria + SaaS → fuera.
    - Pedís X + solo señales de X → OK.

    Cualquier cluster observado fuera de los permitidos basta para rechazar
    (aunque también haya uno permitido: el label de Prospeo no limpia el nombre).
    """
    allowed = icp_industry_clusters(campaign_industry)
    if not allowed:
        return None
    observed = sector_clusters_from_text(
        company_name=company_name, role=role, industry=industry
    )
    if not observed:
        return None
    foreign = observed - allowed
    if foreign:
        return (
            "sector incompatible con el ICP de industria "
            f"(pedido: {', '.join(sorted(allowed))}; "
            f"ajeno: {', '.join(sorted(foreign))})"
        )
    return None


def _industry_index() -> dict[str, IndustryCanon]:
    out: dict[str, IndustryCanon] = {}
    for canon in _INDUSTRY_CANONS:
        out[_norm(canon.key)] = canon
        for alias in canon.aliases:
            out[_norm(alias)] = canon
    return out


_INDUSTRY_INDEX = _industry_index()


def resolve_industry_canon(label: str | None) -> IndustryCanon | None:
    n = _norm(label)
    if not n:
        return None
    hit = _INDUSTRY_INDEX.get(n)
    if hit:
        return hit
    # Frase que contiene un alias completo (ej. "SaaS B2B"), no un token suelto.
    for canon in _INDUSTRY_CANONS:
        for alias in canon.aliases:
            an = _norm(alias)
            if len(an) < 4:
                continue
            if re.search(rf"(?<![a-z]){re.escape(an)}(?![a-z])", n):
                return canon
    return None


def observed_industry(
    industry: str | None,
    *,
    origin: str | None = None,
    campaign_industry: str | None = None,
) -> str | None:
    """Descarta labels copiados del ICP o procedencia icp. No inventa industria."""
    raw = (industry or "").strip() or None
    if not raw:
        return None
    orig = normalize_origin(origin)
    if orig == "icp":
        return None
    if orig not in _OBSERVED_ORIGINS:
        camp = (campaign_industry or "").strip()
        if camp and _norm(raw) == _norm(camp):
            # Histórico: el valor coincide con el ICP actual; puede ser stamp, no evidencia.
            return None
    return raw


def extract_brave_observed_industry(
    *,
    title: str | None,
    snippet: str | None,
    campaign_industry: str | None,
) -> str | None:
    """Industria nombrada en el contenido de Brave, no el label de la consulta ICP.

    Si el snippet solo repite el texto pedido al buscador, no hay evidencia.
    """
    blob = _norm(f"{title or ''} {snippet or ''}")
    if not blob:
        return None
    icp_n = _norm(campaign_industry)
    icp_canon = resolve_industry_canon(campaign_industry)
    hits: list[tuple[IndustryCanon, str]] = []
    for canon in _INDUSTRY_CANONS:
        for alias in canon.aliases:
            an = _norm(alias)
            if len(an) < 4:
                continue
            if icp_n and an == icp_n:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(an)}(?![a-z0-9])", blob):
                hits.append((canon, alias))
                break
    if not hits:
        return None
    for canon, alias in hits:
        if icp_canon and canon.cluster != icp_canon.cluster:
            return alias
    for canon, alias in hits:
        if icp_canon and canon.key == icp_canon.key:
            return alias
    return hits[0][1]


def annotate_company_firmographics(
    company: Any,
    *,
    campaign_industry: str | None,
    campaign_company_size: str | None,
) -> Any:
    """Adjunta estados del clasificador a un CompanyCandidateRead (in-memory)."""
    origin_i = getattr(company, "industry_origin", None)
    origin_s = getattr(company, "size_origin", None)
    firm = assess_company_firmographics(
        campaign_industry=campaign_industry,
        campaign_company_size=campaign_company_size,
        industry=getattr(company, "industry", None),
        industry_origin=origin_i,
        company_size=getattr(company, "company_size", None),
        employee_count=getattr(company, "employee_count", None),
        size_origin=origin_s,
    )
    update = {
        "industry_status": firm.industry.status,
        "size_status": firm.size.status,
        "firmographic_status": firm.combined,
        "firmographic_reason": firm.combined_reason,
    }
    if hasattr(company, "model_copy"):
        return company.model_copy(update=update)
    for key, value in update.items():
        setattr(company, key, value)
    return company


def overlay_company_firmographics(base: Any, other: Any) -> Any:
    """Copia industria/tamaño de `other` a `base` solo si la procedencia es mejor."""
    if base is None:
        return other
    if other is None:
        return base
    update: dict[str, Any] = {}
    if should_write_industry(
        incoming=getattr(other, "industry", None),
        incoming_origin=getattr(other, "industry_origin", None),
        stored=getattr(base, "industry", None),
        stored_origin=getattr(base, "industry_origin", None),
    ):
        update["industry"] = getattr(other, "industry", None)
        update["industry_origin"] = normalize_origin(getattr(other, "industry_origin", None))
    if should_write_size(
        incoming_size=getattr(other, "company_size", None),
        incoming_count=getattr(other, "employee_count", None),
        incoming_origin=getattr(other, "size_origin", None),
        stored_size=getattr(base, "company_size", None),
        stored_count=getattr(base, "employee_count", None),
        stored_origin=getattr(base, "size_origin", None),
    ):
        update["company_size"] = getattr(other, "company_size", None)
        update["employee_count"] = getattr(other, "employee_count", None)
        update["size_origin"] = normalize_origin(getattr(other, "size_origin", None))
    if not update:
        return base
    if hasattr(base, "model_copy"):
        return base.model_copy(update=update)
    for key, value in update.items():
        setattr(base, key, value)
    return base


def annotate_companies_for_icp(
    companies: list[Any],
    *,
    campaign_industry: str | None,
    campaign_company_size: str | None,
    drop_contradictory: bool = True,
) -> list[Any]:
    """Adjunta estados y, si corresponde, saca empresas contradictory del cupo."""
    out: list[Any] = []
    for company in companies:
        annotated = annotate_company_firmographics(
            company,
            campaign_industry=campaign_industry,
            campaign_company_size=campaign_company_size,
        )
        if drop_contradictory and getattr(annotated, "firmographic_status", None) == "contradictory":
            continue
        out.append(annotated)
    return out


# --- Tamaño -------------------------------------------------------------------

_UNBOUNDED = 10**9

_SIZE_LABELS: list[tuple[re.Pattern[str], tuple[int, int]]] = [
    (re.compile(r"\bpyme\b|\bpymes\b", re.I), (11, 200)),
    (re.compile(r"\bstartup\b|\bstart[\s\-]?up\b|\bseed\b|\bmicro\b", re.I), (1, 50)),
    (re.compile(r"\bsmb\b", re.I), (11, 200)),
    (re.compile(r"\bmid[\s\-]?market\b|\bmediana\b", re.I), (51, 1000)),
    (re.compile(r"\benterprise\b|\bgrande\b|\bscale[\s\-]?up\b", re.I), (1000, _UNBOUNDED)),
    (re.compile(r"\bpeque[nñ]a\b", re.I), (11, 50)),
]

# Más específico primero: 11–50 antes que 11–20 no es necesario (patrones distintos).
_SIZE_RANGES: list[tuple[re.Pattern[str], tuple[int, int]]] = [
    (re.compile(r"\b11[\s\-–]*50\b"), (11, 50)),
    (re.compile(r"\b51[\s\-–]*200\b"), (51, 200)),
    (re.compile(r"\b201[\s\-–]*1[\s.]?000\b"), (201, 1000)),
    (re.compile(r"\b1[\s\-–]*10\b"), (1, 10)),
    (re.compile(r"\b11[\s\-–]*20\b"), (11, 20)),
    (re.compile(r"\b21[\s\-–]*50\b"), (21, 50)),
    (re.compile(r"\b51[\s\-–]*100\b"), (51, 100)),
    (re.compile(r"\b101[\s\-–]*200\b"), (101, 200)),
    (re.compile(r"\b201[\s\-–]*500\b"), (201, 500)),
    (re.compile(r"\b501[\s\-–]*1[\s.]?000\b"), (501, 1000)),
    (re.compile(r"\b1001[\s\-–]*2000\b"), (1001, 2000)),
    (re.compile(r"\b2001[\s\-–]*5000\b"), (2001, 5000)),
    (re.compile(r"\b5001[\s\-–]*10[\s.]?000\b"), (5001, 10000)),
    (
        re.compile(
            r"(?:\b(?:1000|1[\s.]?000|1k)\s*\+|\b1001\s*\+|\b10[\s.]?000\s*\+|\b10000\+)",
            re.I,
        ),
        (1000, _UNBOUNDED),
    ),
]


def parse_size_bounds(
    company_size: str | None = None,
    employee_count: int | None = None,
    *,
    prefer_count: bool = False,
) -> tuple[int, int] | None:
    """Interpreta rangos UI / Explorar / labels. ``1000+`` es intervalo, no 1000 exacto."""
    blob = (company_size or "").strip()
    count_bounds: tuple[int, int] | None = None
    if employee_count is not None:
        try:
            n = int(employee_count)
            if n > 0:
                count_bounds = (n, n)
        except (TypeError, ValueError):
            count_bounds = None
    text_bounds: tuple[int, int] | None = None
    if blob:
        for pattern, bounds in _SIZE_LABELS:
            if pattern.search(blob):
                text_bounds = bounds
                break
        if text_bounds is None:
            for pattern, bounds in _SIZE_RANGES:
                if pattern.search(blob):
                    text_bounds = bounds
                    break
        if text_bounds is None:
            nums = [int(x) for x in re.findall(r"\d+", blob.replace(",", ""))]
            if len(nums) >= 2:
                lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
                if lo > 0:
                    text_bounds = (lo, hi)
            elif len(nums) == 1 and nums[0] > 0:
                n = nums[0]
                if re.search(r"\+", blob):
                    text_bounds = (n, _UNBOUNDED)
                else:
                    text_bounds = (n, n)
    if prefer_count:
        return count_bounds or text_bounds
    return text_bounds or count_bounds


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _range_contained(inner: tuple[int, int], outer: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _ranges_adjacent(a: tuple[int, int], b: tuple[int, int], *, slack: int = 1) -> bool:
    if _ranges_overlap(a, b):
        return False
    if a[1] < b[0]:
        return (b[0] - a[1]) <= slack
    if b[1] < a[0]:
        return (a[0] - b[1]) <= slack
    return False


def size_signals_conflict(
    company_size: str | None,
    employee_count: int | None,
) -> bool:
    """True si texto y conteo existen y no se solapan."""
    text = parse_size_bounds(company_size=company_size)
    count = parse_size_bounds(employee_count=employee_count)
    if text is None or count is None:
        return False
    return not _ranges_overlap(text, count)


# --- Resultado ----------------------------------------------------------------

@dataclass(frozen=True)
class DimAssessment:
    status: DimStatus | None
    reason: str
    origin: str | None = None
    canonical: str | None = None
    bounds: tuple[int, int] | None = None
    observed: str | None = None


@dataclass(frozen=True)
class FirmographicAssessment:
    industry: DimAssessment
    size: DimAssessment
    combined: CombinedStatus | None
    combined_reason: str

    @property
    def confirmed(self) -> bool:
        return self.combined == "confirmed"

    @property
    def contradicts_icp(self) -> bool:
        return self.industry.status == "contradictory" or self.size.status == "contradictory"


def _unused_dim(reason: str) -> DimAssessment:
    return DimAssessment(status=None, reason=reason)


def _assess_industry(
    *,
    campaign_industry: str | None,
    industry: str | None,
    industry_origin: str | None,
) -> DimAssessment:
    if campaign_industry is None or is_icp_token_empty(campaign_industry):
        return _unused_dim("ICP industria no configurada")
    orig = normalize_origin(industry_origin)
    observed = observed_industry(
        industry, origin=industry_origin, campaign_industry=campaign_industry
    )
    if orig == "icp" or (
        orig not in _OBSERVED_ORIGINS
        and (industry or "").strip()
        and _norm(industry) == _norm(campaign_industry)
    ):
        return DimAssessment(
            status="incomplete",
            reason="industria copiada del ICP o sin evidencia real",
            origin=orig or "uncertain",
            observed=None,
        )
    if not observed:
        if (industry or "").strip() and orig not in _OBSERVED_ORIGINS:
            return DimAssessment(
                status="review",
                reason="industria histórica sin procedencia confirmada",
                origin=orig or "uncertain",
                observed=(industry or "").strip(),
            )
        return DimAssessment(
            status="incomplete",
            reason="industria desconocida",
            origin=orig,
            observed=None,
        )
    relation = _industry_relation(campaign_industry, observed)
    if orig not in _OBSERVED_ORIGINS:
        if relation.status == "contradictory":
            return DimAssessment(
                status="contradictory",
                reason=relation.reason,
                origin=orig or "uncertain",
                canonical=relation.canonical,
                observed=observed,
            )
        return DimAssessment(
            status="review",
            reason="industria sin procedencia observada",
            origin=orig or "uncertain",
            canonical=relation.canonical,
            observed=observed,
        )
    return DimAssessment(
        status=relation.status,
        reason=relation.reason,
        origin=orig,
        canonical=relation.canonical,
        observed=observed,
    )


def _industry_relation(campaign_industry: str, observed: str) -> DimAssessment:
    icp_canon = resolve_industry_canon(campaign_industry)
    obs_canon = resolve_industry_canon(observed)
    if _norm(observed) == _norm(campaign_industry):
        return DimAssessment(
            status="exact",
            reason="industria coincide",
            canonical=obs_canon.key if obs_canon else None,
            observed=observed,
        )
    if icp_canon and obs_canon and icp_canon.key == obs_canon.key:
        return DimAssessment(
            status="equivalent",
            reason=f"industria equivalente ({observed} ≈ {campaign_industry})",
            canonical=obs_canon.key,
            observed=observed,
        )
    if icp_canon and obs_canon and icp_canon.cluster == obs_canon.cluster:
        return DimAssessment(
            status="similar",
            reason="industria del mismo grupo, no equivalente para este ICP",
            canonical=obs_canon.key,
            observed=observed,
        )
    if icp_canon and obs_canon:
        return DimAssessment(
            status="contradictory",
            reason="industria incompatible con el ICP",
            canonical=obs_canon.key,
            observed=observed,
        )
    return DimAssessment(
        status="contradictory",
        reason="industria no alineada con el ICP",
        observed=observed,
    )


def _assess_size(
    *,
    campaign_size: str | None,
    company_size: str | None,
    employee_count: int | None,
    size_origin: str | None,
) -> DimAssessment:
    if campaign_size is None or is_icp_token_empty(campaign_size):
        return _unused_dim("ICP tamaño no configurado")
    orig = normalize_origin(size_origin)
    target = parse_size_bounds(company_size=campaign_size)
    if target is None:
        return DimAssessment(
            status="incomplete",
            reason="ICP tamaño no interpretable",
            origin=orig,
        )
    if size_signals_conflict(company_size, employee_count):
        return DimAssessment(
            status="review",
            reason="employee_count y company_size no coinciden",
            origin=orig or "uncertain",
            bounds=parse_size_bounds(company_size=company_size),
            observed=(company_size or "").strip() or None,
        )
    actual = parse_size_bounds(company_size=company_size, employee_count=employee_count)
    if actual is None:
        return DimAssessment(
            status="incomplete",
            reason="tamaño desconocido",
            origin=orig,
            bounds=None,
        )
    if orig not in _OBSERVED_ORIGINS:
        # Sin procedencia no se confirma ni se trata el conteo como headcount global.
        if _ranges_overlap(target, actual) or _ranges_adjacent(target, actual):
            return DimAssessment(
                status="review",
                reason="tamaño sin procedencia conocida",
                origin=orig or "uncertain",
                bounds=actual,
                observed=(company_size or "").strip() or (
                    str(employee_count) if employee_count is not None else None
                ),
            )
        return DimAssessment(
            status="review",
            reason="tamaño histórico sin procedencia; no se confirma incompatibilidad",
            origin=orig or "uncertain",
            bounds=actual,
            observed=(company_size or "").strip() or None,
        )

    if _range_contained(actual, target) or actual == target:
        return DimAssessment(
            status="exact",
            reason="tamaño alineado con ICP",
            origin=orig,
            bounds=actual,
            observed=(company_size or "").strip() or (
                str(employee_count) if employee_count is not None else None
            ),
        )
    if _range_contained(target, actual) and actual != target:
        return DimAssessment(
            status="equivalent",
            reason="el rango observado cubre el tamaño ICP",
            origin=orig,
            bounds=actual,
            observed=(company_size or "").strip() or None,
        )
    if _ranges_overlap(target, actual):
        return DimAssessment(
            status="equivalent",
            reason="rangos de tamaño se solapan",
            origin=orig,
            bounds=actual,
            observed=(company_size or "").strip() or None,
        )
    if _ranges_adjacent(target, actual):
        return DimAssessment(
            status="similar",
            reason="tamaño en banda adyacente, no equivalente",
            origin=orig,
            bounds=actual,
            observed=(company_size or "").strip() or None,
        )
    return DimAssessment(
        status="contradictory",
        reason="tamaño incompatible con el ICP",
        origin=orig,
        bounds=actual,
        observed=(company_size or "").strip() or (
            str(employee_count) if employee_count is not None else None
        ),
    )


_CONFIRM = frozenset({"exact", "equivalent"})


def _combine(industry: DimAssessment, size: DimAssessment) -> tuple[CombinedStatus | None, str]:
    active = [d for d in (industry, size) if d.status is not None]
    if not active:
        return None, "ICP sin industria ni tamaño"
    statuses = [d.status for d in active if d.status]
    if "contradictory" in statuses:
        which = "industria" if industry.status == "contradictory" else "tamaño"
        if industry.status == "contradictory" and size.status == "contradictory":
            which = "industria y tamaño"
        return "contradictory", f"{which} incompatible; no se confirma"
    if "review" in statuses:
        return "review", "hay datos ambiguos; no se confirma"
    if "incomplete" in statuses:
        other = [s for s in statuses if s in _CONFIRM]
        if other:
            return "incomplete", "una dimensión confirmada y otra incompleta; no se confirma"
        return "incomplete", "faltan datos firmográficos"
    if "similar" in statuses and set(statuses) <= {"exact", "equivalent", "similar"}:
        return "similar", "hay aproximación; no se usa como coincidencia confirmada"
    if set(statuses) <= _CONFIRM:
        return "confirmed", "industria y tamaño solicitados coinciden"
    return "review", "combinación firmográfica no confirmada"


def assess_company_firmographics(
    *,
    campaign_industry: str | None = None,
    campaign_company_size: str | None = None,
    industry: str | None = None,
    industry_origin: str | None = None,
    company_size: str | None = None,
    employee_count: int | None = None,
    size_origin: str | None = None,
) -> FirmographicAssessment:
    """Clasifica industria y tamaño por separado y combina sin compensación."""
    ind = _assess_industry(
        campaign_industry=campaign_industry,
        industry=industry,
        industry_origin=industry_origin,
    )
    size = _assess_size(
        campaign_size=campaign_company_size,
        company_size=company_size,
        employee_count=employee_count,
        size_origin=size_origin,
    )
    combined, reason = _combine(ind, size)
    return FirmographicAssessment(
        industry=ind,
        size=size,
        combined=combined,
        combined_reason=reason,
    )


def cache_firmographic_inputs(row: Any) -> dict[str, Any]:
    """Lee industria/tamaño del caché sin asumir procedencia histórica.

    Si el contacto no tiene el dato, usa la empresa vinculada. No copia origen
    de la empresa sobre un valor histórico del contacto sin procedencia.
    """
    industry = (getattr(row, "industry", None) or "").strip() or None
    industry_origin = normalize_origin(getattr(row, "industry_origin", None))
    company_size = (getattr(row, "company_size", None) or "").strip() or None
    employee_count = getattr(row, "employee_count", None)
    size_origin = normalize_origin(getattr(row, "size_origin", None))
    employer = getattr(row, "employer", None)
    if employer is not None:
        if not industry:
            industry = (getattr(employer, "industry", None) or "").strip() or None
            industry_origin = normalize_origin(getattr(employer, "industry_origin", None))
        if not company_size and employee_count is None:
            company_size = (getattr(employer, "company_size", None) or "").strip() or None
            employee_count = getattr(employer, "employee_count", None)
            size_origin = normalize_origin(getattr(employer, "size_origin", None))
    return {
        "industry": industry,
        "industry_origin": industry_origin,
        "company_size": company_size,
        "employee_count": employee_count,
        "size_origin": size_origin,
    }


def should_write_industry(
    *,
    incoming: str | None,
    incoming_origin: str | None,
    stored: str | None,
    stored_origin: str | None,
) -> bool:
    """No pisa un dato observado con vacío, ICP o procedencia menos confiable."""
    value = (incoming or "").strip() or None
    if not value:
        return False
    orig = normalize_origin(incoming_origin)
    if orig == "icp":
        return False
    if not (stored or "").strip():
        return orig != "icp"
    return origin_trust(incoming_origin) > origin_trust(stored_origin)


def should_write_size(
    *,
    incoming_size: str | None,
    incoming_count: int | None,
    incoming_origin: str | None,
    stored_size: str | None,
    stored_count: int | None,
    stored_origin: str | None,
) -> bool:
    has_in = bool((incoming_size or "").strip()) or (
        incoming_count is not None and int(incoming_count or 0) > 0
    )
    if not has_in:
        return False
    orig = normalize_origin(incoming_origin)
    if orig == "icp":
        return False
    has_stored = bool((stored_size or "").strip()) or (
        stored_count is not None and int(stored_count or 0) > 0
    )
    if not has_stored:
        return True
    return origin_trust(incoming_origin) > origin_trust(stored_origin)
