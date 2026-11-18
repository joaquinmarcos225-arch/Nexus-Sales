"""Regla 9 — asunto de email con valor del producto (no genérico frío).

Ej.: «Automatizá tu outbound» / «Propuesta breve: Automatizá tu outbound».
«Propuesta breve para {empresa}» solo, sin valor → se reescribe.
"""

from __future__ import annotations

import hashlib
import re

_GENERIC_SUBJECT_RE = re.compile(
    r"(?i)^\s*("
    r"seguimiento|consulta|ventas|hola|"
    r"una\s+idea(?:\s+para\s+.+)?|"
    r"idea\s+para\s+.+|"
    r"propuesta\s+breve(?:\s+para\s+.+)?|"
    r"propuesta\s+breve\s*:?\s*$|"
    r"automatizaci[oó]n\s+de\s+prospecci[oó]n\s+para\s+.+"
    r"|prospecci[oó]n(?:\s+manual)?\s+(?:en|para)\s+.+"
    r"|re:\s*.+"
    r")\s*$"
)

# Asunto genérico “{Producto} para {Empresa}” sin señal de valor.
_PRODUCT_FOR_COMPANY_RE = re.compile(
    r"(?i)^\s*.{2,40}\s+para\s+.{2,42}\s*$"
)

_VALUE_SIGNAL_RE = re.compile(
    r"(?i)\b("
    r"automatiz|outbound|prospecci|ahorr|reduc|tarea|manual|"
    r"secuen|multicanal|pipeline|conversi|entreg|env[ií]o|"
    r"log[ií]stic|costo|eficienc|agend|whatsapp|linkedin|"
    r"integr|orquest|centraliz|roi"
    r")\b"
)

_HOOK_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)automatiz\w*.{0,60}outbound"), "Automatizá tu outbound"),
    (re.compile(r"(?i)automatiz\w*.{0,60}prospecci"), "Automatizá tu prospección"),
    (re.compile(r"(?i)outbound\s+multicanal|multicanal.{0,40}outbound"), "Outbound multicanal"),
    (re.compile(r"(?i)60\s*%|90\s*%|tareas?\s+manual"), "Menos tareas manuales de outbound"),
    (re.compile(r"(?i)secuen\w*.{0,40}(email|linkedin|whatsapp)"), "Secuencias email + LinkedIn + WhatsApp"),
    (re.compile(r"(?i)entreg|env[ií]o|log[ií]stic|last\s*mile|uber\s*direct"), "Simplificá tus envíos"),
    (re.compile(r"(?i)costo|ahorr|reduc\w*\s+gasto"), "Bajá costos operativos"),
)

_SUBJECT_SHAPES: tuple[str, ...] = (
    "{hook}",
    "{hook} para {empresa}",
    "Propuesta breve: {hook}",
    "Idea: {hook}",
)


def _product_blob(product: dict | None) -> str:
    if not product:
        return ""
    parts = [
        str(product.get("value_proposition") or ""),
        str(product.get("interpreted_summary") or ""),
        str(product.get("description") or ""),
        str(product.get("original_description") or ""),
        str(product.get("extracted_benefits") or ""),
        str(product.get("name") or ""),
    ]
    return "\n".join(p for p in parts if p.strip())


def short_value_subject_hook(product: dict | None) -> str:
    """Gancho corto de valor para asunto (máx ~48 chars)."""
    blob = _product_blob(product)
    for pat, hook in _HOOK_RULES:
        if pat.search(blob):
            return hook[:48]
    # Fallback: primeras palabras útiles de la VP / description.
    raw = (
        str((product or {}).get("value_proposition") or "")
        or str((product or {}).get("description") or "")
        or str((product or {}).get("name") or "Una idea breve")
    )
    raw = re.sub(r"\s+", " ", raw).strip()
    # Sacar muleta inicial.
    raw = re.sub(
        r"(?i)^(con\s+\w+\s+|nosotros\s+|desde\s+\w+\s+|nuestra\s+plataforma\s+)",
        "",
        raw,
    ).strip()
    words = raw.split()
    if not words:
        return "Una idea breve"
    # Imperativo suave si empieza con verbo en infinitivo / 3ª persona.
    clip = " ".join(words[:7])
    if len(clip) > 48:
        clip = clip[:45].rstrip() + "…"
    # Capitalizar primera letra.
    return clip[:1].upper() + clip[1:] if clip else "Una idea breve"


def subject_has_value_signal(subject: str) -> bool:
    return bool(_VALUE_SIGNAL_RE.search(subject or ""))


def is_weak_email_subject(subject: str | None, *, allow_re_thread: bool = False) -> bool:
    s = (subject or "").strip()
    if not s:
        return True
    if allow_re_thread and re.match(r"(?i)^re:\s*", s):
        return False
    if subject_has_value_signal(s):
        # «Propuesta breve: Automatizá…» OK
        return False
    if _GENERIC_SUBJECT_RE.match(s):
        return True
    # «Propuesta breve: {nombre de producto}» sin ángulo de valor → débil
    if re.match(r"(?i)^propuesta\s+breve\s*:", s):
        return True
    # "{Algo} para {Empresa}" sin señal de valor → débil
    if _PRODUCT_FOR_COMPANY_RE.match(s):
        return True
    return False


def build_value_email_subject(
    *,
    product: dict | None,
    company: str = "",
    seed: str = "",
) -> str:
    hook = short_value_subject_hook(product)
    empresa = (company or "").strip() or "tu equipo"
    if len(empresa) > 42:
        empresa = empresa.split()[0][:42]
    idx = int(hashlib.md5((seed or hook).encode("utf-8")).hexdigest()[:8], 16) % len(
        _SUBJECT_SHAPES
    )
    shape = _SUBJECT_SHAPES[idx]
    # Si no hay empresa útil, no usar shape «para {empresa}».
    if "{empresa}" in shape and empresa.lower() in {"tu equipo", "tu empresa", ""}:
        shape = "{hook}" if idx % 2 == 0 else "Propuesta breve: {hook}"
    out = shape.format(hook=hook, empresa=empresa).strip()
    return out[:72]


def enforce_value_email_subject(
    *,
    subject: str | None,
    product: dict | None,
    company: str = "",
    seed: str = "",
    sequence_first_touch: bool = True,
    channel: str = "email",
) -> str | None:
    """
    Email: si el asunto es genérico/frío, lo reemplaza por uno con valor del producto.
    En follow-ups con Re: se respeta el hilo.
    """
    ch = (channel or "").strip().lower()
    if ch and ch != "email":
        return subject
    allow_re = not sequence_first_touch
    if not is_weak_email_subject(subject, allow_re_thread=allow_re):
        return (subject or "").strip() or None
    return build_value_email_subject(product=product, company=company, seed=seed)


def email_subject_rules_for_prompt(product: dict | None) -> str:
    hook = short_value_subject_hook(product)
    return (
        "REGLA DURA ASUNTO EMAIL: el subject debe llevar un ángulo corto de VALOR "
        "del producto (ej. estilo «Automatizá tu outbound»), no solo "
        "«Propuesta breve para {empresa}» / «Una idea para…». "
        f"Gancho sugerido desde la ficha: «{hook}». "
        "Variantes OK: el gancho solo, «Propuesta breve: {gancho}», "
        "«{gancho} para {empresa}».\n"
    )
