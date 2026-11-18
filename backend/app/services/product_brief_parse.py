"""Parse determinista de fichas de producto con encabezados (Nombre / Descripción / …).

Si el SDR pega o sube un brief etiquetado, preservamos el contenido completo
sin depender de que la IA lo resuma o pierda secciones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# Clave canónica → aliases de encabezado (línea sola o "Alias:").
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "suggested_name": (
        "nombre",
        "name",
        "producto",
        "servicio",
        "product name",
        "nombre del producto",
    ),
    "description": (
        "descripción",
        "descripcion",
        "description",
        "qué es",
        "que es",
        "about",
        "overview",
    ),
    "value_proposition": (
        "propuesta de valor",
        "value proposition",
        "valor",
        "value prop",
        "vp",
    ),
    "target_audience": (
        "público objetivo",
        "publico objetivo",
        "target audience",
        "audiencia",
        "icp",
        "para quién",
        "para quien",
    ),
    "use_cases": (
        "casos de uso",
        "use cases",
        "casos",
        "use case",
    ),
    "pain_points": (
        "problemas que resuelve / pain points",
        "problemas que resuelve",
        "pain points",
        "pains",
        "dolores",
        "problemas",
    ),
    "main_benefits": (
        "beneficios principales",
        "beneficios",
        "benefits",
        "main benefits",
    ),
    "common_objections": (
        "objeciones comunes",
        "objeciones",
        "objections",
        "common objections",
    ),
    "recommended_tone": (
        "tono recomendado",
        "tono",
        "tone",
        "recommended tone",
    ),
}

_HEADER_RE = re.compile(
    r"^(?P<header>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 /|&+\-]{2,80})\s*:?\s*$",
    re.MULTILINE,
)


def _norm_header(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = s.rstrip(":").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _alias_to_key() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, aliases in _SECTION_ALIASES.items():
        for a in aliases:
            out[_norm_header(a)] = key
    return out


_ALIAS_MAP = _alias_to_key()


@dataclass
class StructuredProductBrief:
    suggested_name: str = ""
    description: str = ""
    value_proposition: str = ""
    target_audience: str = ""
    use_cases: str = ""
    pain_points: str = ""
    main_benefits: str = ""
    common_objections: str = ""
    recommended_tone: str = ""
    sections_found: list[str] = field(default_factory=list)

    @property
    def is_rich(self) -> bool:
        """True si hay suficiente estructura etiquetada para no depender solo de la IA."""
        core = sum(
            1
            for v in (self.suggested_name, self.description, self.value_proposition)
            if (v or "").strip()
        )
        extras = sum(
            1
            for v in (
                self.target_audience,
                self.use_cases,
                self.pain_points,
                self.main_benefits,
                self.common_objections,
                self.recommended_tone,
            )
            if (v or "").strip()
        )
        return core >= 2 or (core >= 1 and extras >= 2) or len(self.sections_found) >= 3

    def as_interpret_dict(self) -> dict[str, str]:
        name = (self.suggested_name or "").strip()
        return {
            "suggested_name": name,
            "name": name,
            "description": (self.description or "").strip(),
            "value_proposition": (self.value_proposition or "").strip(),
            "target_audience": (self.target_audience or "").strip(),
            "use_cases": (self.use_cases or "").strip(),
            "pain_points": (self.pain_points or "").strip(),
            "main_benefits": (self.main_benefits or "").strip(),
            "common_objections": (self.common_objections or "").strip(),
            "recommended_tone": (self.recommended_tone or "").strip(),
        }


def parse_structured_product_brief(raw_text: str | None) -> StructuredProductBrief:
    """
    Parte el texto por líneas-encabezado conocidas.
    Si el mismo encabezado aparece más de una vez, concatena (dedupe de párrafos).
    """
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    brief = StructuredProductBrief()
    if not text:
        return brief

    lines = text.split("\n")
    current_key: str | None = None
    buckets: dict[str, list[str]] = {k: [] for k in _SECTION_ALIASES}
    order: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Encabezado solo en la línea (con o sin ':').
        key = None
        if stripped and len(stripped) <= 80:
            maybe = _norm_header(stripped)
            # "Propuesta de valor:" o "Propuesta de valor"
            key = _ALIAS_MAP.get(maybe)
            if key is None and ":" in stripped:
                left = _norm_header(stripped.split(":", 1)[0])
                key = _ALIAS_MAP.get(left)
                if key is not None:
                    rest = stripped.split(":", 1)[1].strip()
                    current_key = key
                    if key not in order:
                        order.append(key)
                    if rest:
                        buckets[key].append(rest)
                    continue
        if key is not None:
            current_key = key
            if key not in order:
                order.append(key)
            continue
        if current_key is None:
            continue
        if stripped:
            buckets[current_key].append(stripped)
        else:
            # conservar separación de párrafos
            if buckets[current_key] and buckets[current_key][-1] != "":
                buckets[current_key].append("")

    def _join(parts: list[str]) -> str:
        # Colapsar blancos duplicados, preservar párrafos.
        out: list[str] = []
        para: list[str] = []
        for p in parts:
            if p == "":
                if para:
                    out.append(" ".join(para))
                    para = []
            else:
                para.append(p)
        if para:
            out.append(" ".join(para))
        # Dedupe párrafos idénticos (fichas con secciones repetidas).
        seen: set[str] = set()
        uniq: list[str] = []
        for block in out:
            k = re.sub(r"\s+", " ", block).strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            uniq.append(block.strip())
        return "\n\n".join(uniq).strip()

    for key in order:
        val = _join(buckets.get(key) or [])
        if not val:
            continue
        setattr(brief, key, val)
        if key not in brief.sections_found:
            brief.sections_found.append(key)

    # Si suggested_name quedó multilínea, quedate con la primera línea corta.
    if brief.suggested_name and "\n" in brief.suggested_name:
        first = brief.suggested_name.split("\n", 1)[0].strip()
        if 1 <= len(first) <= 120:
            brief.suggested_name = first

    return brief


def merge_interpret_dicts(primary: dict[str, str], fallback: dict[str, str]) -> dict[str, str]:
    """primary gana si tiene valor; si no, usa fallback (p. ej. IA)."""
    keys = (
        "suggested_name",
        "name",
        "description",
        "value_proposition",
        "target_audience",
        "use_cases",
        "pain_points",
        "main_benefits",
        "common_objections",
        "recommended_tone",
    )
    out: dict[str, str] = {}
    for k in keys:
        a = (primary.get(k) or "").strip()
        b = (fallback.get(k) or "").strip()
        out[k] = a or b
    if not out.get("suggested_name") and out.get("name"):
        out["suggested_name"] = out["name"]
    return out
