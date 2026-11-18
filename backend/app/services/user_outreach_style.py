"""Mi estilo — personalización de copy por usuario (SDR)."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_outreach_style import UserOutreachStyle
from app.schemas.user_outreach_style import (
    MAX_STYLE_EXAMPLES,
    UserOutreachStyleRead,
    UserOutreachStyleUpdate,
    _normalize_examples,
)

_CHANNEL_LABELS = {
    "linkedin": "LinkedIn",
    "email": "Email",
    "whatsapp": "WhatsApp",
}


def _loads(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return _normalize_examples([str(x) for x in data])


def _dumps(values: list[str]) -> str:
    return json.dumps(_normalize_examples(values), ensure_ascii=False)


def get_or_create_style(db: Session, user_id: int) -> UserOutreachStyle:
    row = db.scalar(select(UserOutreachStyle).where(UserOutreachStyle.user_id == int(user_id)))
    if row is not None:
        return row
    row = UserOutreachStyle(user_id=int(user_id))
    db.add(row)
    db.flush()
    return row


def read_style(db: Session, user_id: int) -> UserOutreachStyleRead:
    row = db.scalar(select(UserOutreachStyle).where(UserOutreachStyle.user_id == int(user_id)))
    if row is None:
        return UserOutreachStyleRead()
    return UserOutreachStyleRead(
        country=(row.country or None),
        examples_linkedin=_loads(row.examples_linkedin),
        examples_email=_loads(row.examples_email),
        examples_whatsapp=_loads(row.examples_whatsapp),
    )


def update_style(db: Session, user_id: int, payload: UserOutreachStyleUpdate) -> UserOutreachStyleRead:
    row = get_or_create_style(db, user_id)
    country = (payload.country or "").strip() or None
    row.country = country
    row.examples_linkedin = _dumps(payload.examples_linkedin)
    row.examples_email = _dumps(payload.examples_email)
    row.examples_whatsapp = _dumps(payload.examples_whatsapp)
    from app.services.seller_style_ai import load_templates, refresh_templates

    user = db.get(User, int(user_id))
    company = getattr(user, "company", None) if user is not None else None
    row.templates_json = json.dumps(
        refresh_templates(
            load_templates(getattr(row, "templates_json", None)),
            examples_for_channel(row, None),
            sender=_seller_display_name(user) if user is not None else "",
            brand=(getattr(company, "name", None) or "").strip(),
        ),
        ensure_ascii=False,
    )
    db.flush()
    return read_style(db, user_id)


def style_record_for_user(db: Session, user_id: int | None) -> UserOutreachStyle | None:
    if not user_id:
        return None
    return db.scalar(select(UserOutreachStyle).where(UserOutreachStyle.user_id == int(user_id)))


def examples_for_channel(row: UserOutreachStyle | None, channel: str | None) -> list[str]:
    if row is None:
        return []
    ch = (channel or "").strip().lower()
    if ch == "linkedin":
        return _loads(row.examples_linkedin)
    if ch == "email":
        return _loads(row.examples_email)
    if ch == "whatsapp":
        return _loads(row.examples_whatsapp)
    out: list[str] = []
    for blob in (row.examples_linkedin, row.examples_email, row.examples_whatsapp):
        out.extend(_loads(blob))
    return _normalize_examples(out)


def _seller_display_name(user: User | None) -> str:
    if user is None:
        return "SDR"
    first = (getattr(user, "first_name", None) or "").strip()
    if first:
        return first
    return (getattr(user, "name", None) or "SDR").strip() or "SDR"


def build_style_prompt_block(
    *,
    user: User | None,
    style: UserOutreachStyle | None,
    channel: str | None = None,
) -> str:
    """
    Bloque para prompts: país + ejemplos del canal.
    La IA infiere saludo, largo, CTA y tono desde los ejemplos (no campos sueltos).
    """
    if style is None:
        return ""
    name = _seller_display_name(user)
    parts: list[str] = [
        f"[Estilo personal · {name}]",
        "Estos mensajes son referencia de ESTE SDR. Copiá su tono, largo, saludo, "
        "presentación y cierre — adaptá nombres/empresa/producto al prospecto actual. "
        "No copies literal palabra por palabra. En campañas grandes, cada prospecto "
        "debe sonar levemente distinto (ancla, orden, sinónimos) con el mismo estilo.",
    ]
    country = (style.country or "").strip()
    if country:
        parts.append(f"País/región del SDR: {country}")
        from app.services.outreach_language import dialect_from_country, dialect_prompt_block

        parts.append(dialect_prompt_block(dialect_from_country(country)).strip())
    ch = (channel or "").strip().lower()
    channels: list[tuple[str, list[str]]] = []
    if ch in _CHANNEL_LABELS:
        ex = examples_for_channel(style, ch)
        if ex:
            channels.append((_CHANNEL_LABELS[ch], ex))
    else:
        for key, label in _CHANNEL_LABELS.items():
            ex = examples_for_channel(style, key)
            if ex:
                channels.append((label, ex))
    if not channels:
        return ""
    for label, examples in channels:
        parts.append(f"--- Ejemplos {label} ({len(examples)}) ---")
        for i, ex in enumerate(examples, start=1):
            trimmed = ex.strip()
            if len(trimmed) > 1800:
                trimmed = trimmed[:1800].rstrip() + "…"
            parts.append(f"Ejemplo {i}:\n{trimmed}")
    return "\n".join(parts)


def extract_question_cta(text: str) -> str | None:
    """Última línea con ? en un ejemplo (CTA típico)."""
    for line in reversed([ln.strip() for ln in (text or "").splitlines() if ln.strip()]):
        if "?" in line:
            return line
    return None


def extract_opening_lines(text: str, *, max_lines: int = 3) -> list[str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[:max_lines]


def style_has_channel_examples(style: UserOutreachStyle | None, channel: str) -> bool:
    return bool(examples_for_channel(style, channel))
