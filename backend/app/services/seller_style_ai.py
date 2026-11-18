"""Ejemplos de «Mi estilo» → estructura con [Nombre]/[Rol]/[Empresa] (IA, una vez por ejemplo)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"\[(?:Nombre|Rol|Empresa)\]")
_WORD_RE = re.compile(r"[\w&\-]+")

_SYSTEM = (
    "Recibís un mensaje de prospección que un vendedor escribió como EJEMPLO, dirigido a una persona "
    "de ejemplo. Tu única tarea: reemplazar los datos de ESA persona de ejemplo por marcadores.\n"
    "- Su nombre o apellido → [Nombre]\n"
    "- Su cargo/rol/puesto (ej. «Director comercial», «Gerente de Ventas», «CEO») → [Rol]\n"
    "- La empresa donde trabaja (ej. «X», «Acme», «Mercado Libre») → [Empresa]\n"
    "NO toques el nombre del vendedor que firma o se presenta («Soy …»), ni su empresa, ni su producto. "
    "NO cambies ninguna otra palabra, tilde, puntuación ni salto de línea. "
    "Si una frase usa el dato dos veces, reemplazá las dos. Si no hay datos de la persona, devolvé el texto igual.\n"
    'Respondé solo JSON: {"template": "<texto>"}'
)


def example_key(example: str | None) -> str:
    return hashlib.sha1((example or "").strip().encode("utf-8")).hexdigest()[:16]


def _fold_words(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return _WORD_RE.findall(t)


def _template_is_faithful(original: str, template: str) -> bool:
    """La IA solo puede sacar palabras (las que pasan a marcador), nunca inventar otras."""
    orig = _fold_words(original)
    kept = _fold_words(_MARKER_RE.sub(" ", template))
    if not kept or len(kept) < 0.6 * len(orig):
        return False
    pool: dict[str, int] = {}
    for w in orig:
        pool[w] = pool.get(w, 0) + 1
    for w in kept:
        if pool.get(w, 0) <= 0:
            return False
        pool[w] -= 1
    return template.count("\n") == original.strip().count("\n")


def convert_example(example: str, *, sender: str = "", brand: str = "") -> str | None:
    from app.services.openai_service import _raw_chat, openai_configured

    text = (example or "").strip()
    if not text or not openai_configured():
        return None
    hints = ", ".join(x for x in (sender, brand) if x)
    user = (f"Vendedor / su empresa: {hints}\n\n" if hints else "") + f"Mensaje:\n{text}"
    try:
        raw = _raw_chat(_SYSTEM, user, temperature=0.0, max_output_tokens=900).strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        template = str(json.loads(raw).get("template") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mi estilo: no se pudo convertir ejemplo (%s)", exc)
        return None
    if not template or not _template_is_faithful(text, template):
        return None
    return template


def load_templates(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def refresh_templates(
    current: dict[str, str],
    examples: Iterable[str],
    *,
    sender: str = "",
    brand: str = "",
) -> dict[str, str]:
    """Convierte solo los ejemplos nuevos; descarta los que ya no están."""
    wanted = {example_key(ex): ex for ex in examples if (ex or "").strip()}
    out = {k: v for k, v in current.items() if k in wanted}
    todo = [(k, ex) for k, ex in wanted.items() if k not in out]
    if not todo:
        return out
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda item: convert_example(item[1], sender=sender, brand=brand), todo))
    for (k, _), tpl in zip(todo, results):
        if tpl:
            out[k] = tpl
    return out


_FAILED_KEYS: set[str] = set()


def templated_example(style: Any, example: str) -> str:
    """Versión con marcadores del ejemplo (la arma la IA la primera vez y queda guardada)."""
    if not hasattr(style, "templates_json"):
        return example
    mapping = load_templates(getattr(style, "templates_json", None))
    key = example_key(example)
    if key in mapping:
        return mapping[key]
    if key in _FAILED_KEYS:
        return example
    tpl = convert_example(example)
    if not tpl:
        _FAILED_KEYS.add(key)
        return example
    mapping[key] = tpl
    try:
        style.templates_json = json.dumps(mapping, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return tpl
