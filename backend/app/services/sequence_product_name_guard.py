"""Regla 10 — no martillar el nombre del producto (suena a IA).

Por mensaje: como máximo 1 mención del nombre de producto.
En follow-ups: si ya se nombró en toques previos, preferir 0 (sacar el resto).
"""

from __future__ import annotations

import re


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


def _product_terms(product_name: str, brand: str = "") -> list[str]:
    name = re.sub(r"\s+", " ", (product_name or "").strip())
    if not name or len(name) < 2:
        return []
    terms = [name]
    # Si marca ≠ producto, no contar la marca sola como producto
    # (ej. «Soy Javier de Uber» vs producto «Uber Direct»).
    return terms


def prior_product_mention_count(
    prior_touches: list[dict] | None,
    *,
    product_name: str,
) -> int:
    terms = _product_terms(product_name)
    if not terms:
        return 0
    n = 0
    for touch in prior_touches or []:
        body = str(touch.get("body") or touch.get("message_body") or "")
        n += mention_count(body, terms)
    return n


def _find_spans(text: str, term: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", text or "", flags=re.I):
        spans.append(m.span())
    return spans


def _replace_mention_at(text: str, start: int, end: int, term: str) -> str:
    """Reemplaza una mención concreta por una forma natural sin el nombre."""
    before = text[:start]
    after = text[end:]
    # Prefijos comunes justo antes del match (se comen prefijo + nombre).
    prefixes = (
        (r"(?i)\bsobre\s+$", ""),
        (r"(?i)\blo\s+de\s+$", "eso "),
        (r"(?i)\bcon\s+$", "con esto "),
        (r"(?i)\bdesde\s+$", ""),
        (r"(?i)\bde\s+$", "de esto "),
        (r"(?i)\bver\s+$", "verlo "),
        (r"(?i)\bexplorar\s+$", "explorarlo "),
        (r"(?i)\bmostrarte\s+$", "mostrarte lo que hacemos "),
        (r"(?i)\bayudarte\s+con\s+$", "ayudarte con esto "),
        (r"(?i)\bllamada\s+corta\s+sobre\s+$", "llamada corta "),
        (r"(?i)\bcharla\s+corta\s+sobre\s+$", "charla corta "),
        (r"(?i)\bcharla\s+breve\s+sobre\s+$", "charla breve "),
    )
    tail = before[-48:]
    for pat, rep in prefixes:
        m = re.search(pat, tail)
        if m:
            pref_len = len(m.group(0))
            return before[:-pref_len] + rep + after.lstrip()
    return before + "esto" + after


def limit_product_name_in_body(
    body: str,
    *,
    product_name: str,
    max_mentions: int = 1,
) -> str:
    """Deja como máximo `max_mentions` apariciones del nombre de producto."""
    text = (body or "").replace("\r\n", "\n")
    name = re.sub(r"\s+", " ", (product_name or "").strip())
    if not name or max_mentions < 0:
        return text.strip()
    spans = _find_spans(text, name)
    if len(spans) <= max_mentions:
        return text.strip()

    # Conservar las primeras max_mentions; reemplazar de atrás hacia adelante.
    for start, end in reversed(spans[max_mentions:]):
        text = _replace_mention_at(text, start, end, name)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +([.,;:])", r"\1", text)
    text = re.sub(r"\.\.+", ".", text)
    return text.strip()


def enforce_product_name_limit(
    *,
    body: str,
    product_name: str,
    prior_touches: list[dict] | None = None,
    sequence_first_touch: bool = False,
    brand: str = "",
) -> str:
    """
    Regla 10 dura:
    - 1º toque: máx. 1 mención del nombre de producto en el body.
    - Follow-up: si ya se nombró antes → 0; si no → máx. 1.
    """
    text = (body or "").strip()
    name = re.sub(r"\s+", " ", (product_name or "").strip())
    if not text or not name:
        return text

    # Si marca == producto, la presentación «Soy X de Marca» ya cuenta.
    brand_n = re.sub(r"\s+", " ", (brand or "").strip())
    presentation_counts = bool(
        brand_n
        and brand_n.lower() == name.lower()
        and re.search(rf"(?i)\b(?:soy|te escribo desde)\b[^\n]{{0,40}}\b{re.escape(name)}\b", text)
    )

    if sequence_first_touch:
        max_n = 1
    elif prior_product_mention_count(prior_touches, product_name=name) > 0:
        max_n = 0
    else:
        max_n = 1

    if presentation_counts and max_n == 1:
        # La presentación ya usó el cupo: el resto del body a 0 menciones extra
        # → limitamos a 1 total (la de Soy…).
        max_n = 1

    return limit_product_name_in_body(text, product_name=name, max_mentions=max_n)


def product_name_rules_for_prompt(
    *,
    product_name: str,
    prior_touches: list[dict] | None = None,
    sequence_first_touch: bool = False,
) -> str:
    name = (product_name or "").strip() or "el producto"
    prior_n = prior_product_mention_count(prior_touches, product_name=name)
    lines = [
        f"REGLA DURA NOMBRE DE PRODUCTO: no martilles «{name}» "
        "(ni el nombre del producto en general). Suena a IA.",
        "Máximo 1 mención del nombre de producto por mensaje.",
    ]
    if not sequence_first_touch and prior_n > 0:
        lines.append(
            f"Ya se nombró en toques previos ({prior_n}×): en este follow-up "
            "NO lo nombres; usá «esto», «lo que hacemos», «lo de la charla»."
        )
    else:
        lines.append(
            "Si hace falta nombrarlo, una sola vez; después referí sin repetir el nombre."
        )
    return "\n".join(lines) + "\n"
