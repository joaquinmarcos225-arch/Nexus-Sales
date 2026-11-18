"""Regla 3b — puente ancla («Vi que sos…») → valor del producto.

En el 1º toque: no dejar ancla y valor cortados en párrafos secos.
Si detecta el corte, los une con puente natural. La IA no puede saltearlo.
"""

from __future__ import annotations

import re

from app.services.message_structure_variants import connect_prospect_anchor_to_value

_ANCHOR_PARA_RE = re.compile(
    r"(?is)^\s*("
    r"Vi que sos\b[^.!?\n]*[.!]?"
    r"|Vi que est[aá]s en\b[^.!?\n]*[.!]?"
    r"|Vi tu lugar como\b[^.!?\n]*[.!]?"
    r"|Me cruz[eé] con tu perfil\b[^.!?\n]*[.!]?"
    r")\s*$"
)

_VALUE_OPEN_RE = re.compile(
    r"(?i)^\s*("
    r"(?:nosotros\s+)?(?:con|desde)\b|"
    r"te\s+resumo\b|"
    r"en\s+la\s+pr[aá]ctica\b|"
    r"la\s+idea\b|"
    r"hoy\b|"
    r"lo\s+que\s+buscamos\b|"
    r"habilitamos\b|automatizamos\b|ayudamos\b|centralizamos\b|orquestamos\b"
    r")"
)


def _paras(body: str) -> list[str]:
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def has_disconnected_anchor_value(body: str) -> bool:
    """True si hay ancla en un párrafo y valor en el siguiente, sin puente."""
    paras = _paras(body)
    for i in range(len(paras) - 1):
        if _ANCHOR_PARA_RE.match(paras[i]) and _VALUE_OPEN_RE.match(paras[i + 1]):
            return True
    # Misma línea: «Vi que sos X. Con Producto…» (punto + valor, sin conector)
    if re.search(
        r"(?i)\bVi que sos\b[^.!?\n]+[.!]\s+(?:nosotros\s+)?(?:con|desde)\b",
        body or "",
    ):
        return True
    return False


def bridge_anchor_to_value_in_body(
    body: str,
    *,
    seed: str = "",
) -> str:
    """Une párrafos ancla→valor desconectados."""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    paras = _paras(text)
    if len(paras) < 2:
        # Caso «Vi que sos X. Con Y…» en el mismo párrafo
        m = re.search(
            r"(?is)(\bVi que sos\b[^.!?\n]+[.!])\s+((?:nosotros\s+)?(?:con|desde)\b.+)$",
            text,
        )
        if m:
            bridged = connect_prospect_anchor_to_value(
                m.group(1).strip(),
                m.group(2).strip(),
                seed=seed or "bridge1",
                separate_paragraphs=False,
            )
            return (text[: m.start()] + bridged + text[m.end() :]).strip()
        return text

    out: list[str] = []
    i = 0
    while i < len(paras):
        if (
            i + 1 < len(paras)
            and _ANCHOR_PARA_RE.match(paras[i])
            and _VALUE_OPEN_RE.match(paras[i + 1])
        ):
            bridged = connect_prospect_anchor_to_value(
                paras[i],
                paras[i + 1],
                seed=f"{seed}|{i}" if seed else f"bridge|{i}",
                separate_paragraphs=False,
            )
            # Si connect devolvió dos párrafos (no continuable), forzar un puente suave.
            if "\n\n" in bridged:
                a = paras[i].rstrip(".!? ").strip()
                v = paras[i + 1].strip()
                if v and v[0].isupper():
                    v = v[0].lower() + v[1:]
                bridged = f"{a}, y {v}"
            out.append(bridged)
            i += 2
            continue
        out.append(paras[i])
        i += 1
    return "\n\n".join(out).strip()


def enforce_anchor_value_bridge(
    *,
    body: str,
    sequence_first_touch: bool,
    seed: str = "",
) -> str:
    """Solo en 1º toque: si ancla y valor están cortados, los conecta."""
    text = (body or "").strip()
    if not text or not sequence_first_touch:
        return text
    if not has_disconnected_anchor_value(text):
        return text
    return bridge_anchor_to_value_in_body(text, seed=seed)


def anchor_bridge_rules_for_prompt(*, sequence_first_touch: bool) -> str:
    if not sequence_first_touch:
        return ""
    return (
        "REGLA DURA PUENTE ANCLA→VALOR: si usás «Vi que sos {rol} de/en {empresa}», "
        "lo que sigue tiene que conectar en la misma idea "
        "(ej. «Vi que sos {rol} en {empresa}, y con {producto}…»). "
        "PROHIBIDO corte seco: ancla sola + párrafo nuevo «Con {producto}…». "
        "No inventes dolores del rol.\n"
    )
