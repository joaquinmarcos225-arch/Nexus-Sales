"""1º toque: el mensaje tiene que decir la oferta/valor del producto, no solo el problema.

Ejemplo malo: «con Contractia sabemos que el desafío es…» (diagnóstico, sin oferta).
Ejemplo bueno: «con Contractia centralizamos plantillas, aprobaciones y firma digital…».
"""

from __future__ import annotations

import re

from app.services.outreach_display_names import product_outreach_display_name
from app.services.sequence_followup_value_guard import _insert_value_before_cta

# Verbos de oferta (qué hacemos / qué ofrecemos).
_OFFER_VERB_RE = re.compile(
    r"(?i)\b(?:"
    r"ayudamos|ofrecemos|centralizamos|automatizamos|permitimos|digitalizamos|"
    r"gestionamos|orquestamos|aceleramos|acortamos|integramos|implementamos|"
    r"habilitamos|bajamos|reducimos|operamos|simplificamos|unificamos|"
    r"organizamos|trazamos|resguardamos|validamos\s+identidad|"
    r"hacemos|logramos|resolvemos|cubrimos"
    r")\b"
)

# Sustantivos / resultados que anclan la oferta (no solo el dolor).
_OFFER_NOUN_RE = re.compile(
    r"(?i)\b(?:"
    r"firma(?:s)?(?:\s+digital(?:es)?)?|contratos?|documentaci[oó]n|documentos?|"
    r"plantillas?|flujos?|aprobaciones?|trazabilidad|ciclo|"
    r"outbound|prospecci[oó]n|entregas?|stock|inventario|automatizaci[oó]n|"
    r"plataforma|software|herramienta|gesti[oó]n\s+documental|"
    r"identidad|resguardo|pendientes"
    r")\b"
)

# Framing de solo-problema / empatía sin oferta.
_PROBLEM_FRAME_RE = re.compile(
    r"(?i)\b(?:"
    r"desaf[ií]o|el problema|lo dif[ií]cil|cuesta|duele|"
    r"no est[aá] solamente|sino en todo lo que sucede|"
    r"sabemos que|entendemos que|conocemos que|"
    r"suele\s+(?:costar|pasar|haber)|muchas\s+veces\s+cuesta"
    r")\b"
)


def body_has_clear_product_offer(body: str) -> bool:
    """True si el prospecto entiende qué le ofrecen (no solo el dolor)."""
    text = (body or "").strip()
    if len(text) < 40:
        return False
    has_verb = bool(_OFFER_VERB_RE.search(text))
    has_noun = bool(_OFFER_NOUN_RE.search(text))
    if has_verb and has_noun:
        return True
    # Verbo de oferta + suficiente sustancia, aunque el noun no matchee el regex.
    if has_verb and len(text) >= 80:
        # Evitar «sabemos que…» / «entendemos que…» disfrazados de oferta.
        if _PROBLEM_FRAME_RE.search(text) and not has_noun:
            return False
        return True
    return False


def _product_offer_line(product: dict | None, *, seed: str = "") -> str:
    """Una oración de valor anclada a la ficha (sin inventar)."""
    from app.services.cold_message_bank import _valor_one_liner

    pname = product_outreach_display_name((product or {}).get("name") if product else None)
    if not pname:
        pname = str((product or {}).get("name") or "").strip() or "nuestra solución"
    line = _valor_one_liner(
        product,
        product_name=pname,
        market="b2b",
        channel="email",  # 1º toque: oración completa de oferta (también en WA)
        explain_more=True,
        seed=seed or f"ft-value|{pname}",
    ).strip()
    if body_has_clear_product_offer(line):
        return line
    # Nuclear desde VP/description de la ficha.
    vp = re.sub(
        r"\s+",
        " ",
        str(
            (product or {}).get("value_proposition")
            or (product or {}).get("description")
            or ""
        ).strip(),
    )
    if vp:
        clause = vp[0].lower() + vp[1:] if vp[0].isupper() else vp
        # Recortar a ~1-2 oraciones.
        parts = re.split(r"(?<=[.!?])\s+", clause)
        clause = " ".join(parts[:2]).strip()
        if len(clause) > 220:
            clause = clause[:217].rsplit(" ", 1)[0].rstrip(",;") + "."
        if not clause.endswith((".", "!", "?")):
            clause += "."
        return f"Con {pname}, {clause}"
    return (
        f"Con {pname} centralizamos el trabajo de punta a punta "
        f"para que el equipo avance sin fricción entre áreas."
    )


def _dedupe_product_name_opener(offer: str, *, body: str, product_name: str) -> str:
    """Si el body ya nombró el producto, no abrir otra vez con «Con X…»."""
    name = (product_name or "").strip()
    line = (offer or "").strip()
    if not name or not line:
        return line
    if not re.search(rf"(?i)\b{re.escape(name)}\b", body or ""):
        return line
    stripped = re.sub(
        rf"(?i)^(?:con|desde)\s+{re.escape(name)}\s*[,:]?\s+",
        "",
        line,
    ).strip()
    if stripped and stripped[0].islower():
        stripped = stripped[0].upper() + stripped[1:]
    return stripped or line


def enforce_first_touch_product_offer(
    *,
    body: str,
    product: dict | None,
    sequence_first_touch: bool,
    seed: str = "",
) -> str:
    """
    1º toque: si solo hay problema/diagnóstico, inserta la oferta de la ficha
    antes del CTA. Follow-ups no tocan (ya tienen su propio guard).
    """
    text = (body or "").strip()
    if not text or not sequence_first_touch:
        return text
    if body_has_clear_product_offer(text):
        return text
    offer = _product_offer_line(product, seed=seed)
    if not offer:
        return text
    pname = product_outreach_display_name((product or {}).get("name") if product else None) or ""
    offer = _dedupe_product_name_opener(offer, body=text, product_name=pname)
    return _insert_value_before_cta(text, offer)
