"""Alineación cargo ICP vs cargo real del contacto — prospección, auditoría y mensajes."""

from __future__ import annotations

import re
from typing import Any, Literal

from app.schemas.mvp_outreach import RoleAlignmentRead
from app.services.lead_sourcing.icp_mapper import _split_titles

AlignmentLevel = Literal["match", "partial", "mismatch", "unknown"]
RoleTier = Literal["exact", "equivalent", "similar"]
ROLE_TIER_ORDER: tuple[RoleTier, ...] = ("exact", "equivalent", "similar")

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
    """Solo los cargos que pidió el usuario: los alias («Owner», «President») traían otros roles."""
    out: list[str] = []
    seen: set[str] = set()
    for title in icp_role_titles(icp_target_role):
        t = title.strip()
        if t and _norm(t) not in seen:
            seen.add(_norm(t))
            out.append(t)
    return out[:10]


_TITLE_PREFIX_BLOCK = frozenset({"sub", "vice", "deputy", "adjunto", "adjunta"})
_TITLE_ROLE_BLOCK = frozenset(
    {
        "assistant", "asistente", "secretary", "secretaria", "intern", "pasante",
        "becario", "becaria", "trainee", "ex", "former",
    }
)


def _norm_text(value: str | None) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", str(value or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def title_matches(role: str | None, titles: list[str]) -> bool:
    """El cargo contiene alguno de los pedidos como frase completa, sin «sub», «asistente», «ex»…"""
    tokens = _norm_text(role).split()
    if not tokens:
        return False
    for title in titles:
        wanted = _norm_text(title).split()
        if not wanted:
            continue
        blocked = _TITLE_ROLE_BLOCK - set(wanted)
        if blocked & set(tokens):
            continue
        n = len(wanted)
        for i in range(len(tokens) - n + 1):
            if tokens[i : i + n] != wanted:
                continue
            if i > 0 and tokens[i - 1] in _TITLE_PREFIX_BLOCK and tokens[i - 1] not in wanted:
                continue
            return True
    return False


_TITLE_STOPWORDS = frozenset({"de", "del", "la", "el", "los", "las", "of", "the", "y", "and", "e", "en", "para", "for", "at", "a"})
_FEMININE_TO_BASE = {
    "jefa": "jefe",
    "socia": "socio",
    "presidenta": "presidente",
    "gerenta": "gerente",
    "duena": "dueno",
}
_BASE_TO_FEMININE = {
    "director": "directora",
    "coordinador": "coordinadora",
    "fundador": "fundadora",
    "cofundador": "cofundadora",
    "administrador": "administradora",
    "contador": "contadora",
    "jefe": "jefa",
    "socio": "socia",
    "presidente": "presidenta",
    "encargado": "encargada",
    "ejecutivo": "ejecutiva",
    "dueno": "duena",
    "dueño": "dueña",
}
_TITLE_LOOSE_EXTRA_SPAN = 3


def _gender_base(token: str) -> str:
    if token in _FEMININE_TO_BASE:
        return _FEMININE_TO_BASE[token]
    for fem, base in (("ora", "or"), ("ada", "ado"), ("iva", "ivo")):
        if len(token) > len(fem) + 2 and token.endswith(fem):
            return token[: -len(fem)] + base
    return token


def title_matches_loose(role: str | None, titles: list[str]) -> bool:
    """Como ``title_matches`` pero acepta femenino, otro orden y palabras de unión.

    «Directora Comercial» ≈ «Director Comercial», «Sales Head» ≈ «Head of Sales»,
    «Gerente de Ventas» ≈ «Gerente Ventas». Sigue rechazando sub/vice/asistente/ex.
    """
    raw_tokens = _norm_text(role).split()
    if not raw_tokens:
        return False
    tokens = [_gender_base(t) for t in raw_tokens]
    for title in titles:
        wanted_raw = _norm_text(title).split()
        if not wanted_raw:
            continue
        blocked = _TITLE_ROLE_BLOCK - set(wanted_raw)
        if blocked & set(raw_tokens):
            continue
        wanted = [_gender_base(t) for t in wanted_raw if t not in _TITLE_STOPWORDS] or [
            _gender_base(t) for t in wanted_raw
        ]
        positions: list[int] = []
        ok = True
        for w in wanted:
            idx = next(
                (
                    i
                    for i, t in enumerate(tokens)
                    if t == w and not (i > 0 and raw_tokens[i - 1] in _TITLE_PREFIX_BLOCK and raw_tokens[i - 1] not in wanted_raw)
                ),
                None,
            )
            if idx is None:
                ok = False
                break
            positions.append(idx)
        if not ok:
            continue
        if max(positions) - min(positions) <= len(wanted) - 1 + _TITLE_LOOSE_EXTRA_SPAN:
            return True
    return False


def feminine_title_variants(title: str) -> list[str]:
    """«Director Comercial» → ["Directora Comercial"] (para pedirle a Prospeo ambas formas)."""
    words = (title or "").split()
    changed = False
    out: list[str] = []
    for w in words:
        key = w.lower()
        fem = _BASE_TO_FEMININE.get(key)
        if fem:
            out.append(fem.capitalize() if w[:1].isupper() else fem)
            changed = True
        else:
            out.append(w)
    return [" ".join(out)] if changed else []


# Sinónimos de cargo para ampliar Explorar sin traer otros roles: solo equivalencias reales
# (mismo puesto en otro idioma o con otro nombre de área), nunca un nivel distinto.
_EXEC_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("CEO", "Gerente General", "Director General", "Chief Executive Officer"),
    ("Dueño", "Owner", "Propietario"),
    ("Fundador", "Founder"),
    ("Cofundador", "Co-Founder", "Cofounder"),
    ("CFO", "Director Financiero", "Chief Financial Officer"),
    ("COO", "Director de Operaciones", "Chief Operating Officer"),
    ("CTO", "Director de Tecnología", "Chief Technology Officer"),
    ("CMO", "Director de Marketing", "Chief Marketing Officer"),
)

# rol → (formas en español, formas en inglés)
_ROLE_FORMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gerente": (("Gerente",), ("Manager",)),
    "jefe": (("Jefe",), ("Head",)),
    "director": (("Director",), ("Director",)),
    "coordinador": (("Coordinador",), ("Coordinator",)),
}
_ROLE_TOKENS = {
    "gerente": "gerente", "manager": "gerente",
    "jefe": "jefe", "head": "jefe",
    "director": "director",
    "coordinador": "coordinador", "coordinator": "coordinador",
}

# área → (formas en español «de X» , adjetivo español sin «de», formas en inglés)
_AREA_FORMS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "ventas": (("Ventas",), ("Comercial",), ("Sales",)),
    "marketing": (("Marketing",), (), ("Marketing",)),
    "operaciones": (("Operaciones",), (), ("Operations",)),
    "finanzas": (("Finanzas", "Administración y Finanzas"), ("Financiero",), ("Finance",)),
    "rrhh": (("Recursos Humanos", "RRHH"), (), ("HR", "Human Resources", "People")),
    "tecnologia": (("Tecnología", "Sistemas", "IT"), (), ("IT", "Technology")),
    "compras": (("Compras", "Abastecimiento"), (), ("Procurement", "Purchasing")),
    "logistica": (("Logística",), (), ("Logistics", "Supply Chain")),
    "negocios": (("Desarrollo de Negocios",), (), ("Business Development",)),
    "producto": (("Producto",), (), ("Product",)),
}
_AREA_PHRASES: dict[str, str] = {
    "ventas": "ventas", "comercial": "ventas", "sales": "ventas", "commercial": "ventas",
    "marketing": "marketing", "mercadeo": "marketing",
    "operaciones": "operaciones", "operations": "operaciones",
    "finanzas": "finanzas", "financiero": "finanzas", "finance": "finanzas",
    "administracion y finanzas": "finanzas",
    "recursos humanos": "rrhh", "rrhh": "rrhh", "hr": "rrhh", "human resources": "rrhh", "people": "rrhh",
    "tecnologia": "tecnologia", "sistemas": "tecnologia", "it": "tecnologia", "technology": "tecnologia",
    "compras": "compras", "abastecimiento": "compras", "procurement": "compras", "purchasing": "compras",
    "logistica": "logistica", "logistics": "logistica", "supply chain": "logistica",
    "desarrollo de negocios": "negocios", "business development": "negocios",
    "producto": "producto", "product": "producto",
}


_JOIN_WORDS = frozenset({"de", "del", "of", "the", "y", "and"})


def _content_words(text: str) -> list[str]:
    return [w for w in _norm_text(text).split() if w not in _JOIN_WORDS]


_AREA_LOOKUP: dict[str, str] = {" ".join(_content_words(p)): a for p, a in _AREA_PHRASES.items()}


def _role_and_area(title: str) -> tuple[str, str] | None:
    """«Gerente Comercial» → ("gerente", "ventas"). Solo si el cargo es exactamente rol + área."""
    words = _content_words(title)
    if len(words) < 2:
        return None
    role_idx = [i for i, w in enumerate(words) if _gender_base(w) in _ROLE_TOKENS]
    if len(role_idx) != 1:
        return None
    role = _ROLE_TOKENS[_gender_base(words[role_idx[0]])]
    area = _AREA_LOOKUP.get(" ".join(w for i, w in enumerate(words) if i != role_idx[0]))
    if area is None:
        return None
    return role, area


def title_synonym_variants(title: str) -> list[str]:
    """Cargos equivalentes: «Gerente Comercial» → «Gerente de Ventas», «Sales Manager», …"""
    norm = _norm_text(title)
    if not norm:
        return []
    for group in _EXEC_SYNONYMS:
        if norm in {_norm_text(g) for g in group}:
            return [g for g in group if _norm_text(g) != norm]

    parsed = _role_and_area(title)
    if parsed is None:
        return []
    role, area = parsed
    role_es, role_en = _ROLE_FORMS[role]
    area_es, area_adj, area_en = _AREA_FORMS[area]
    out: list[str] = []
    for r in role_es:
        out.extend(f"{r} {a}" for a in area_adj)
        out.extend(f"{r} de {a}" for a in area_es)
    for r in role_en:
        out.extend(f"{a} {r}" for a in area_en)
        if r in ("Head", "Director"):
            out.extend(f"{r} of {a}" for a in area_en)
    seen = {norm}
    uniq: list[str] = []
    for v in out:
        k = _norm_text(v)
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq


# Head/Jefe ↔ Director del mismo área (equivalencia de liderazgo, no de cualquier
# cargo que contenga «Sales» / «Comercial»). Gerente queda fuera: es similar.
_LEADERSHIP_EQUIV_ROLES = frozenset({"jefe", "director"})
_SIMILAR_ROLE_KEYS = frozenset({"gerente", "coordinador", "jefe", "director"})
_SIMILAR_LEADERSHIP_TOKENS = frozenset(
    {
        "vp",
        "vicepresidente",
        "vicepresident",
        "cro",
        "chief",
        "lead",
        "lider",
        "gerente",
        "manager",
        "coordinador",
        "coordinator",
        "jefe",
        "head",
        "director",
    }
)
_PRE_SALES_RE = re.compile(
    r"\b(pre\s+sales|presales|pre\s+venta|preventa|pre\s+sale)\b"
)
_SUBORDINATE_RE = re.compile(
    r"\b(subdirector|subgerente|subjefe|sub\s+director|sub\s+gerente|sub\s+jefe)\b"
)


def _titles_for_role_area(role_key: str, area: str) -> list[str]:
    role_es, role_en = _ROLE_FORMS[role_key]
    area_es, area_adj, area_en = _AREA_FORMS[area]
    out: list[str] = []
    for r in role_es:
        out.extend(f"{r} {a}" for a in area_adj)
        out.extend(f"{r} de {a}" for a in area_es)
    for r in role_en:
        out.extend(f"{a} {r}" for a in area_en)
        if r in ("Head", "Director"):
            out.extend(f"{r} of {a}" for a in area_en)
    return out


def equivalent_title_variants(title: str) -> list[str]:
    """Equivalencias reales: sinónimos bilingües + Head/Jefe ↔ Director del mismo área."""
    base = title_synonym_variants(title)
    parsed = _role_and_area(title)
    extra: list[str] = []
    if parsed is not None:
        role, area = parsed
        if role in _LEADERSHIP_EQUIV_ROLES:
            for other in _LEADERSHIP_EQUIV_ROLES:
                if other == role:
                    continue
                extra.extend(_titles_for_role_area(other, area))
    seen = {_norm_text(title)}
    uniq: list[str] = []
    for v in base + extra:
        k = _norm_text(v)
        if k and k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq


def prospeo_equivalent_title_includes(icp_target_role: str | None) -> list[str]:
    """Títulos para la consulta de nivel 2 (equivalentes), sin repetir los exactos."""
    typed = prospeo_role_title_includes(icp_target_role)
    seen = {_norm_text(t) for t in typed}
    out: list[str] = []
    for title in typed:
        for variant in equivalent_title_variants(title):
            key = _norm_text(variant)
            if key and key not in seen:
                seen.add(key)
                out.append(variant)
            if len(out) >= 10:
                return out
    return out


def _area_key_from_title(title: str) -> str | None:
    parsed = _role_and_area(title)
    if parsed is not None:
        return parsed[1]
    words = _content_words(title)
    if not words:
        return None
    for length in range(len(words), 0, -1):
        for i in range(0, len(words) - length + 1):
            phrase = " ".join(words[i : i + length])
            area = _AREA_LOOKUP.get(phrase)
            if area:
                return area
    return None


def _incompatible_for_icp(prospect_role: str, icp_titles: list[str]) -> bool:
    """Pre-Sales, subdirector, asistentes: no entran a exacto ni equivalente por una palabra."""
    p_norm = _norm_text(prospect_role)
    p_tokens = set(p_norm.split())
    wanted: set[str] = set()
    for title in icp_titles:
        wanted.update(_norm_text(title).split())
    icp_blob = " ".join(_norm_text(t) for t in icp_titles)
    if _PRE_SALES_RE.search(p_norm) and not _PRE_SALES_RE.search(icp_blob):
        return True
    blocked_roles = _TITLE_ROLE_BLOCK - wanted
    if blocked_roles & p_tokens:
        return True
    if _SUBORDINATE_RE.search(p_norm) and not _SUBORDINATE_RE.search(icp_blob):
        return True
    return False


def _same_area_similar(icp_titles: list[str], prospect_role: str) -> bool:
    prospect_area = _area_key_from_title(prospect_role)
    if not prospect_area:
        return False
    icp_areas = {a for t in icp_titles if (a := _area_key_from_title(t))}
    if prospect_area not in icp_areas:
        return False
    p_tokens = set(_content_words(prospect_role))
    p_gendered = {_gender_base(t) for t in p_tokens}
    if p_gendered & _SIMILAR_LEADERSHIP_TOKENS:
        return True
    if {"vice", "president"} <= p_gendered or {"vice", "presidente"} <= p_gendered:
        return True
    parsed = _role_and_area(prospect_role)
    return parsed is not None and parsed[0] in _SIMILAR_ROLE_KEYS


def classify_role_tier(
    icp_role: str | None,
    prospect_role: str | None,
) -> RoleTier | None:
    """Nivel 1 exacto, nivel 2 equivalente, nivel 3 similar. None = rechazar."""
    titles = icp_role_titles(icp_role)
    prospect = (prospect_role or "").strip()
    if not titles:
        return "exact" if prospect else None
    if not prospect:
        return None
    if _incompatible_for_icp(prospect, titles):
        return None

    if title_matches(prospect, titles) or title_matches_loose(prospect, titles):
        return "exact"

    equiv: list[str] = []
    seen_eq: set[str] = set()
    for title in titles:
        for variant in equivalent_title_variants(title):
            key = _norm_text(variant)
            if key and key not in seen_eq:
                seen_eq.add(key)
                equiv.append(variant)
    if equiv and (
        title_matches(prospect, equiv) or title_matches_loose(prospect, equiv)
    ):
        return "equivalent"

    parsed_pr = _role_and_area(prospect)
    for title in titles:
        parsed_icp = _role_and_area(title)
        if not parsed_icp or not parsed_pr:
            continue
        icp_role_key, icp_area = parsed_icp
        pr_role_key, pr_area = parsed_pr
        if icp_area != pr_area:
            continue
        if (
            icp_role_key in _LEADERSHIP_EQUIV_ROLES
            and pr_role_key in _LEADERSHIP_EQUIV_ROLES
        ):
            return "equivalent"

    if _same_area_similar(titles, prospect):
        return "similar"
    return None


def select_by_role_tiers(
    items: list[Any],
    *,
    icp_role: str | None,
    get_role: Any,
    limit: int,
) -> list[Any]:
    """Rellena el cupo por nivel: exactos, después equivalentes, después similares."""
    limit = max(0, int(limit))
    if limit <= 0 or not items:
        return []
    buckets: dict[RoleTier, list[Any]] = {tier: [] for tier in ROLE_TIER_ORDER}
    for item in items:
        role = get_role(item) if callable(get_role) else None
        tier = classify_role_tier(icp_role, role)
        if tier:
            buckets[tier].append(item)
    out: list[Any] = []
    for tier in ROLE_TIER_ORDER:
        remain = limit - len(out)
        if remain <= 0:
            break
        out.extend(buckets[tier][:remain])
    return out


def campaign_role_matches(campaign_role: str | None, prospect_role: str | None) -> bool:
    """Misma regla que el buscador: el cargo real contiene uno de los cargos de la campaña."""
    titles = icp_role_titles(campaign_role)
    if not titles:
        return True
    return title_matches(prospect_role, titles)


def normalized_role_for_persist(
    role: str | None,
    *,
    icp_target_role: str | None = None,
) -> str | None:
    """Cargo original compactado; si hay match ICP fuerte, el título canónico."""
    raw = " ".join(str(role).split()) if role else ""
    if not raw:
        return None
    if icp_target_role:
        score, title = best_icp_role_match(icp_target_role, raw)
        if score >= 70 and (title or "").strip():
            return str(title).strip()[:255]
    return raw[:255]


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
