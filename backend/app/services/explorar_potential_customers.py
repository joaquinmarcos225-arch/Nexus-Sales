"""Explorar · «Posibles clientes»: traduce un producto del usuario a filtros de comprador.

La IA elige cargos, industrias, tamaño y nivel solo entre valores que Prospeo acepta.
Sin OpenAI se usa el ICP de la última campaña del producto o un default de decisores.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.product import Product
from app.services.explorar_filter_catalog import (
    DEPARTMENT_OPTIONS,
    HEADCOUNT_OPTIONS,
    INDUSTRY_OPTIONS,
    SENIORITY_OPTIONS,
    sanitize_industry_values,
)

_logger = logging.getLogger(__name__)

DECISION_MAKERS = ["Founder/Owner", "C-Suite", "Vice President", "Head", "Director"]
DEFAULT_SEARCH_LOCATION = "LATAM (sin Brasil)"

_US_MARKERS = (
    "estados unidos",
    "united states",
    "usa",
    "u.s.a",
    "ee.uu",
    "eeuu",
    "north america",
    "norteamérica",
    "norteamerica",
)
_EU_MARKERS = ("europa", "europe", "emea")
_LATAM_MARKERS = (
    "latam",
    "latinoam",
    "américa latina",
    "america latina",
    "hispanoam",
    "cono sur",
    "méxico",
    "mexico",
    "argentina",
    "colombia",
    "chile",
    "perú",
    "peru",
    "uruguay",
)
_BR_EXCLUDE_MARKERS = (
    "sin brasil",
    "sin incluir brasil",
    "excepto brasil",
    "excepto brazil",
    "excluy",
    "no brasil",
    "no brazil",
    "without brazil",
    "excluding brazil",
)

_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _text_has_marker(blob: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if " " in marker or len(marker) >= 5:
            if marker in blob:
                return True
        elif re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", blob):
            return True
    return False


def location_from_product_text(text: str | None) -> str:
    """Región de búsqueda a partir de lo que el cliente escribió en el producto.

    Si no dice otra cosa, LatAm hispano sin Brasil (Prospeo sin región trae yankees).
    """
    low = re.sub(r"\s+", " ", (text or "").lower())
    has_latam = _text_has_marker(low, _LATAM_MARKERS)
    has_us = _text_has_marker(low, _US_MARKERS)
    has_eu = _text_has_marker(low, _EU_MARKERS)
    excludes_br = _text_has_marker(low, _BR_EXCLUDE_MARKERS) or (
        "brasil" in low and "sin " in low
    )
    if has_us and not has_latam and not excludes_br:
        return "United States"
    if has_eu and not has_latam:
        return "Europa"
    if has_latam or excludes_br:
        if ("brasil" in low or "brazil" in low) and not excludes_br:
            return "LATAM + Brasil"
        return DEFAULT_SEARCH_LOCATION
    return DEFAULT_SEARCH_LOCATION


def location_from_product(product: Product | None) -> str:
    if product is None:
        return DEFAULT_SEARCH_LOCATION
    return location_from_product_text(_product_text(product))


def _product_text(product: Product) -> str:
    parts = [
        f"Nombre: {product.name}",
        f"Descripción: {product.description or ''}",
        f"Propuesta de valor: {product.value_proposition or ''}",
        f"A quién apunta: {product.target_notes or ''}",
    ]
    return "\n".join(p for p in parts if p.split(":", 1)[1].strip())[:6000]


def _pick(values: Any, allowed: tuple[str, ...], limit: int) -> list[str]:
    lookup = {a.lower(): a for a in allowed}
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        hit = lookup.get(str(v or "").strip().lower())
        if hit and hit not in out:
            out.append(hit)
        if len(out) >= limit:
            break
    return out


def _titles(values: Any, limit: int = 6) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        t = re.sub(r"\s+", " ", str(v or "")).strip()[:80]
        if t and t.lower() not in {o.lower() for o in out}:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def _normalize(data: dict[str, Any], *, fallback_location: str = DEFAULT_SEARCH_LOCATION) -> dict[str, Any]:
    titles = _titles(data.get("titles"))
    location = re.sub(r"\s+", " ", str(data.get("location") or "").strip())[:80]
    return {
        "titles": titles,
        "industries": sanitize_industry_values(
            _pick(data.get("industries"), INDUSTRY_OPTIONS, 6),
            limit=6,
        ),
        "headcounts": _pick(data.get("headcounts"), HEADCOUNT_OPTIONS, 6),
        "seniorities": _pick(data.get("seniorities"), SENIORITY_OPTIONS, 6) or list(DECISION_MAKERS),
        # Prospeo combina filtros con AND: área + cargo recorta de más (clínicas: 447 vs miles).
        "departments": [] if titles else _pick(data.get("departments"), DEPARTMENT_OPTIONS, 4),
        "location": location or fallback_location,
        "summary": str(data.get("summary") or "").strip()[:280],
    }


def _ai_filters(product: Product) -> dict[str, Any] | None:
    from app.services.openai_service import _raw_chat, openai_configured

    if not openai_configured():
        return None
    system = (
        "Sos experto en prospección B2B en LatAm. Te paso un producto o servicio y devolvés a QUIÉN venderle: "
        "las personas que lo comprarían o decidirían la compra. Respondé SOLO un objeto JSON con estas claves:\n"
        '- "titles": 4 a 6 cargos del comprador, cortos, como aparecen en LinkedIn (mezclá español e inglés, '
        'ej. "Gerente Comercial", "Head of Sales").\n'
        f'- "industries": 2 a 6 industrias de los clientes, SOLO de esta lista exacta: {list(INDUSTRY_OPTIONS)}. '
        "Si el producto sirve a casi cualquier industria, devolvé [].\n"
        f'- "headcounts": rangos de empleados de las empresas cliente, SOLO de: {list(HEADCOUNT_OPTIONS)}.\n'
        f'- "seniorities": niveles del comprador, SOLO de: {list(SENIORITY_OPTIONS)}.\n'
        f'- "departments": 1 a 3 áreas del comprador, SOLO de: {list(DEPARTMENT_OPTIONS)}.\n'
        '- "location": región de los CLIENTES (dónde opera el comprador), no la sede del vendedor. '
        'Si el producto vende en LatAm / Latinoamérica / sin Brasil, usá exactamente "LATAM (sin Brasil)". '
        "PROHIBIDO dejarla vacía. PROHIBIDO Estados Unidos, United States o NA salvo que el producto venda ahí.\n"
        '- "summary": una frase en español rioplatense que explique a quién buscamos y por qué.\n'
        "Priorizá traer MUCHOS candidatos buenos: no restrinjas de más."
    )
    try:
        raw = _raw_chat(system, _product_text(product), temperature=0.2, max_output_tokens=600)
    except Exception as e:  # noqa: BLE001 — sin IA caemos al fallback
        _logger.warning("explorar potential customers: IA falló: %s", e)
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out = _normalize(data, fallback_location=location_from_product(product))
    if not out["titles"] and not out["industries"]:
        return None
    return out


def _campaign_filters(db: Session, product: Product) -> dict[str, Any] | None:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.product_id == product.id, Campaign.target_role.is_not(None))
        .order_by(Campaign.id.desc())
    ).first()
    role = (getattr(campaign, "target_role", None) or "").strip() if campaign else ""
    if not role:
        return None
    titles = _titles(re.split(r"[,;/]| y | o ", role))
    country = (getattr(campaign, "target_country", None) or "").strip()
    return {
        "titles": titles,
        "industries": [],
        "headcounts": [],
        "seniorities": [],
        "departments": [],
        "location": country or location_from_product(product),
        "summary": f"Mismo perfil que la campaña «{campaign.name}»: {role}.",
    }


_SNAPSHOT_KEYS = (
    "titles",
    "industries",
    "headcounts",
    "seniorities",
    "departments",
    "location",
    "summary",
    "source",
)


def _product_hash(product: Product) -> str:
    return hashlib.sha256(_product_text(product).encode("utf-8")).hexdigest()


def _public_filters(product: Product, out: dict[str, Any]) -> dict[str, Any]:
    payload = {k: out.get(k) for k in _SNAPSHOT_KEYS}
    payload["location"] = (str(payload.get("location") or "").strip() or location_from_product(product))
    payload["source"] = payload.get("source") or "snapshot"
    payload["product_id"] = product.id
    payload["product_name"] = product.name
    return payload


def _cheap_filters(db: Session, product: Product) -> dict[str, Any]:
    out = _campaign_filters(db, product)
    source = "campaign"
    if out is None:
        out = {
            "titles": [],
            "industries": [],
            "headcounts": [],
            "seniorities": list(DECISION_MAKERS),
            "departments": [],
            "location": location_from_product(product),
            "summary": "Dueños y directivos con poder de decisión.",
        }
        source = "default"
    out["source"] = source
    out["location"] = (out.get("location") or "").strip() or location_from_product(product)
    return out


def _compute_filters(db: Session, product: Product) -> dict[str, Any]:
    out = _ai_filters(product)
    source = "ai"
    if out is None:
        out = _cheap_filters(db, product)
        source = str(out.get("source") or "default")
    out["source"] = source
    out["location"] = (out.get("location") or "").strip() or location_from_product(product)
    return out


def _persist_filters(product: Product, out: dict[str, Any]) -> None:
    product.potential_filters = {k: out.get(k) for k in _SNAPSHOT_KEYS}
    product.potential_filters_hash = _product_hash(product)
    clear_cache()


def stored_potential_filters(product: Product) -> dict[str, Any] | None:
    raw = getattr(product, "potential_filters", None)
    stored_hash = getattr(product, "potential_filters_hash", None)
    if not isinstance(raw, dict) or not stored_hash:
        return None
    if stored_hash != _product_hash(product):
        return None
    return _public_filters(product, raw)


def refresh_product_potential_filters(db: Session, product: Product) -> dict[str, Any]:
    """Arma y guarda la foto. Solo al crear/editar producto (gasta OpenAI acá)."""
    out = _compute_filters(db, product)
    _persist_filters(product, out)
    return _public_filters(product, out)


def schedule_product_potential_filters(product_id: int) -> None:
    """No bloquea Guardar producto: la foto se escribe en background."""

    def run() -> None:
        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            row = db.get(Product, product_id)
            if row is None:
                return
            refresh_product_potential_filters(db, row)
            db.commit()
        except Exception:  # noqa: BLE001 — el alta del producto ya se guardó
            _logger.exception("No se pudo armar Posibles clientes del producto %s", product_id)
        finally:
            db.close()

    threading.Thread(target=run, daemon=True, name=f"product-filters-{product_id}").start()


def potential_customer_filters(db: Session, product: Product) -> dict[str, Any]:
    """Search/campaña: lee la foto guardada. Sin OpenAI."""
    stored = stored_potential_filters(product)
    if stored is not None:
        return stored
    return _public_filters(product, _cheap_filters(db, product))


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
