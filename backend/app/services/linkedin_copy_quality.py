"""Lint y recorte de copy LinkedIn: empresa y producto, una vez cada uno."""

from __future__ import annotations

import re

from app.services.outreach_display_names import company_label_for_outreach

_OPORTUNO_RE = re.compile(
    r"(?i)me pareci[oó] oportuno escribirte por tu lugar como"
)
_TWO_BEAT_INTRO_RE = re.compile(
    r"(?i)te contacto desde [^.\n]+\.\s*soy [^.\n]+\."
)


def company_aliases(company: str | None) -> list[str]:
    """Formas con las que el copy puede nombrar la misma empresa."""
    raw = re.sub(r"\s+", " ", (company or "").strip())
    if not raw:
        return []
    label = company_label_for_outreach(raw) or raw
    out: list[str] = []
    for term in (raw, label):
        if term and term not in out:
            out.append(term)
    m = re.match(r"^([A-ZÁÉÍÓÚÑ0-9]{2,8})\s*[-–—|:]", raw)
    if m:
        acr = m.group(1)
        if acr not in out:
            out.append(acr)
    m2 = re.match(r"^(.+?)\s+([A-ZÁÉÍÓÚÑ]{2,8})$", raw)
    if m2:
        acr = m2.group(2)
        if acr not in out:
            out.append(acr)
    if label != raw:
        m3 = re.match(r"^(.+?)\s+([A-ZÁÉÍÓÚÑ]{2,8})$", label)
        if m3 and m3.group(2) not in out:
            out.append(m3.group(2))
        initials = "".join(w[0] for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", label))
        if 2 <= len(initials) <= 8 and initials.upper() not in {a.upper() for a in out}:
            out.append(initials.upper())
    return out


def mention_count(text: str, terms: list[str]) -> int:
    """Apariciones no superpuestas; gana el término más largo."""
    low = text or ""
    if not low:
        return 0
    cleaned = [t.strip() for t in terms if t and len(t.strip()) >= 2]
    if not cleaned:
        return 0
    spans: list[tuple[int, int]] = []
    for term in sorted(set(cleaned), key=len, reverse=True):
        for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", low, flags=re.I):
            start, end = m.span()
            if any(start < pe and end > ps for ps, pe in spans):
                continue
            spans.append((start, end))
    return len(spans)


def linkedin_copy_issues(
    text: str | None,
    *,
    company: str = "",
    product: str = "",
) -> list[str]:
    """Motivos por los que el mensaje de LinkedIn suena raro / repetido."""
    if not isinstance(text, str):
        return []
    body = text.strip()
    company = company if isinstance(company, str) else ""
    product = product if isinstance(product, str) else ""
    if not body:
        return []
    issues: list[str] = []
    if _OPORTUNO_RE.search(body):
        issues.append("linkedin: ancla robótica («me pareció oportuno… por tu lugar como»)")
    if _TWO_BEAT_INTRO_RE.search(body):
        issues.append("linkedin: presentación partida («Te contacto desde X. Soy Y.»)")
    product_name = (product or "").strip()
    if product_name and mention_count(body, [product_name]) >= 2:
        issues.append(f"linkedin: el producto «{product_name}» se nombra más de una vez")
    else:
        offered = _offered_product_name(body)
        if offered and mention_count(body, [offered]) >= 2:
            issues.append(f"linkedin: el producto «{offered}» se nombra más de una vez")
    aliases = company_aliases(company)
    if aliases and mention_count(body, aliases) >= 2:
        issues.append("linkedin: la empresa del prospecto se nombra más de una vez")
    return issues


def linkedin_copy_is_repetitive(
    text: str | None,
    *,
    company: str = "",
    product: str = "",
) -> bool:
    return bool(linkedin_copy_issues(text, company=company, product=product))


def polish_linkedin_outbound(
    text: str | None,
    *,
    company: str = "",
    product: str = "",
) -> str:
    """
    Recorte mecánico: una mención de empresa, una de producto.
    No reescribe el mensaje entero; eso lo hace la generación.
    """
    if not isinstance(text, str):
        return text or ""
    body = text.replace("\r\n", "\n").strip()
    company = company if isinstance(company, str) else ""
    product = product if isinstance(product, str) else ""
    if not body:
        return body
    label = company_label_for_outreach(company)
    raw = re.sub(r"\s+", " ", (company or "").strip())
    if raw and label and raw != label:
        body = re.sub(re.escape(raw), label, body, flags=re.I)

    product_name = (product or "").strip() or _offered_product_name(body)
    if product_name and mention_count(body, [product_name]) >= 2:
        body = _strip_product_from_retomo(body, product_name)
        if mention_count(body, [product_name]) >= 2:
            body = re.sub(
                rf"(?i)nosotros ofrecemos {re.escape(product_name)} para",
                "Ayudamos a",
                body,
                count=1,
            )

    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r" {2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _offered_product_name(text: str) -> str:
    m = re.search(
        r"(?i)(?:nosotros ofrecemos|con)\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+){0,3})\s+"
        r"(?:para|ayudamos|armamos|centralizamos|increment)",
        text or "",
    )
    return (m.group(1).strip() if m else "")


def _strip_product_from_retomo(text: str, product: str) -> str:
    escaped = re.escape(product)
    patterns = (
        rf"(?i)(te hab[ií]a escrito por \w+) sobre {escaped}\.?",
        rf"(?i)(retomo lo que te (?:dec[ií]a|comentaba)(?: por \w+)?) sobre {escaped}\.?",
        rf"(?i)(retomo el mail anterior) sobre {escaped}\.?",
        rf"(?i)(vuelvo sobre el mensaje anterior)(?: de {escaped})?\.?",
    )
    out = text
    for pat in patterns:
        out = re.sub(pat, r"\1.", out)
    return out
