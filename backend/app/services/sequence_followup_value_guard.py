"""Follow-ups (excepto el último / breakup): deben traer un ángulo de valor NUEVO.

No alcanza con «te escribí por X · ¿agendamos?».
El último toque (p. ej. día ≥16) puede ir casi vacío.
"""

from __future__ import annotations

import re

from app.services.sequence_cta_guard import extract_meeting_ctas
from app.services.sequence_value_once_guard import (
    extract_value_sentences,
    is_value_sentence,
    prior_value_fingerprints,
    sentence_repeats_prior,
    _token_set,
    _jaccard,
)

_LAST_TOUCH_DAY = 16

_ANGLE_SPLIT_RE = re.compile(r"(?<=[.!;])\s+|\n+|,\s+(?=y\s+)|;\s+")


def is_last_followup(*, step_day: int = 0, prior_touches: list[dict] | None = None) -> bool:
    """Último toque de secuencia típica / breakup: puede ir sin valor nuevo."""
    day = int(step_day or 0)
    if day >= _LAST_TOUCH_DAY:
        return True
    # Sin día explícito: a partir del 5º toque ya es tardío; el “último” real
    # lo marca el playbook por día. Acá solo day>=16 es duro.
    del prior_touches
    return False


def _product_blob(product: dict | None) -> str:
    if not product:
        return ""
    return re.sub(
        r"\s+",
        " ",
        " ".join(
            str(product.get(k) or "")
            for k in (
                "value_proposition",
                "interpreted_summary",
                "description",
                "extracted_benefits",
                "benefits",
            )
        ),
    ).strip()


def _facet_angles_from_product(product: dict | None) -> list[str]:
    """Ángulos cortos anclados a hechos de la ficha (no inventados)."""
    blob = _product_blob(product)
    if not blob:
        return []
    low = blob.lower()
    facets: list[str] = []
    if re.search(r"same[\s-]?day|mismo d[ií]a|entreg", low):
        facets.append(
            "La entrega puede salir el mismo día, sin que armes flota fija."
        )
    if re.search(r"canal propio|vende desde|vendiendo desde|tienda", low):
        facets.append(
            "Seguís vendiendo desde tu canal; la última milla la cubre la red."
        )
    if re.search(r"flota", low):
        facets.append(
            "Suma capacidad en picos sin depender de una flota propia."
        )
    if re.search(r"repartidor|red de uber|red de", low):
        facets.append(
            "La red de repartidores cubre la última milla por vos."
        )
    if re.search(r"automatiz|60\s*%|90\s*%|tarea", low):
        facets.append(
            "Automatizamos buena parte del trabajo manual de outbound."
        )
    if re.search(r"email|linkedin|whatsapp|multicanal", low):
        facets.append(
            "Orquestamos el contacto por email, LinkedIn y WhatsApp en un solo flujo."
        )
    if re.search(r"firma|contrat|documental|aprobaci|gesti[oó]n documental", low):
        facets.append(
            "Centralizamos plantillas, aprobaciones y firma digital en un solo flujo."
        )
        facets.append(
            "Dejamos trazabilidad de pendientes y resguardo de la documentación."
        )
    return facets


def _product_angle_candidates(product: dict | None) -> list[str]:
    if not product:
        return []
    # Primero facets cortos (mejor variedad entre toques); después cláusulas de la ficha.
    out = list(_facet_angles_from_product(product))
    blob = _product_blob(product)
    if not blob:
        return out
    parts = [p.strip(" .,;") for p in _ANGLE_SPLIT_RE.split(blob) if p and len(p.strip()) > 28]
    for p in parts:
        words = p.split()
        if len(words) > 28:
            p = " ".join(words[:22]).rstrip(",;") + "."
        if not p.endswith((".", "!", "?")):
            p += "."
        low = p.lower()
        if low.startswith(("automatiza ", "incrementa ", "habilita ", "permite ")):
            p = re.sub(
                r"^(automatiza|incrementa|habilita|permite)\b",
                lambda m: {
                    "automatiza": "Automatizamos",
                    "incrementa": "Incrementamos",
                    "habilita": "Habilitamos",
                    "permite": "Permitimos",
                }[m.group(1).lower()],
                p,
                count=1,
                flags=re.I,
            )
        out.append(p)
    # Dedup preservando orden
    seen: set[str] = set()
    uniq: list[str] = []
    for a in out:
        key = a.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)
    return uniq[:16]


def pick_new_value_angle(
    *,
    product: dict | None,
    prior_touches: list[dict] | None,
    allow_soft_overlap: bool = False,
) -> str:
    prior_fps = prior_value_fingerprints(prior_touches)
    ranked: list[tuple[float, str]] = []
    for angle in _product_angle_candidates(product):
        toks = _token_set(angle)
        if len(toks) < 4:
            continue
        if sentence_repeats_prior(angle, prior_fps):
            continue
        overlap = max((_jaccard(toks, fp) for fp in prior_fps), default=0.0)
        ranked.append((overlap, angle))
    ranked.sort(key=lambda x: x[0])
    for overlap, angle in ranked:
        if overlap < 0.55:
            return angle
    # Si el FU está vacío de valor, preferimos el menos solapado aunque no sea ideal.
    if allow_soft_overlap and ranked:
        overlap, angle = ranked[0]
        if overlap < 0.88:
            # Acortar a una idea (primera cláusula) para no re-pegar el pitch.
            short = re.split(r",\s+|\s+y\s+", angle, maxsplit=1)[0].strip(" .,")
            if len(_token_set(short)) >= 4:
                return short[0].upper() + short[1:] + "."
            return angle
    return ""


def body_has_fresh_value(body: str, *, prior_touches: list[dict] | None) -> bool:
    prior_fps = prior_value_fingerprints(prior_touches)
    for sent in extract_value_sentences(body):
        if not sentence_repeats_prior(sent, prior_fps):
            return True
    return False


def _insert_value_before_cta(body: str, value_line: str) -> str:
    text = (body or "").replace("\r\n", "\n").strip()
    if not text or not value_line:
        return text
    ctas = extract_meeting_ctas(text)
    line = value_line.strip()
    if not line.endswith((".", "!", "?")):
        line += "."
    if ctas:
        cta = ctas[-1]
        idx = text.rfind(cta)
        if idx >= 0:
            before = text[:idx].rstrip()
            after = text[idx:]
            return f"{before}\n\n{line}\n\n{after}".strip()
    # Antes de «Quedo atento»
    m = re.search(r"(?im)^quedo atento\b.*$", text)
    if m:
        return f"{text[: m.start()].rstrip()}\n\n{line}\n\n{text[m.start():]}".strip()
    return f"{text}\n\n{line}".strip()


def enforce_followup_value_angle(
    *,
    body: str,
    product: dict | None,
    prior_touches: list[dict] | None,
    sequence_first_touch: bool,
    step_day: int = 0,
) -> str:
    """En FU no-final: si no hay ángulo nuevo, inserta uno de la ficha (sin inventar)."""
    text = (body or "").strip()
    if not text or sequence_first_touch:
        return text
    if is_last_followup(step_day=step_day, prior_touches=prior_touches):
        return text
    if body_has_fresh_value(text, prior_touches=prior_touches):
        return text
    angle = pick_new_value_angle(
        product=product,
        prior_touches=prior_touches,
        allow_soft_overlap=True,
    )
    if not angle:
        return text
    return _insert_value_before_cta(text, angle)
