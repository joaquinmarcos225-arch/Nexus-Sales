"""Sugerencia de canal por prospecto según datos y prioridad de campaña (sin conectores reales)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.campaign_channels import normalize_allowed_channels

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.prospect import Prospect


def _norm_phone(phone: str | None) -> bool:
    if not phone:
        return False
    return bool("".join(ch for ch in phone if ch.isdigit()))


def compute_preferred_channel(prospect: Prospect, campaign: Campaign) -> tuple[str, str]:
    """
    Devuelve (channel, reason) con channel en {linkedin, email, whatsapp}.
    Respeta el orden de allowed_channels de la campaña; elige el primer canal
    para el que el prospecto tenga datos suficientes.
    """
    try:
        order = normalize_allowed_channels(getattr(campaign, "allowed_channels", None) or [])
    except ValueError:
        order = ["linkedin", "email", "whatsapp"]

    has_li = bool((prospect.linkedin_url or "").strip())
    has_mail = bool((prospect.email or "").strip())
    has_wa = _norm_phone(getattr(prospect, "whatsapp", None)) or _norm_phone(prospect.phone)

    for ch in order:
        if ch == "linkedin" and has_li:
            return (
                "linkedin",
                "Perfil LinkedIn disponible; prioridad según estrategia de campaña.",
            )
        if ch == "email" and has_mail:
            return "email", "Email disponible; prioridad según estrategia de campaña."
        if ch == "whatsapp" and has_wa:
            return "whatsapp", "Teléfono disponible para WhatsApp; prioridad según estrategia de campaña."

    if has_mail:
        return "email", "Canal sugerido por datos disponibles (email)."
    if has_li:
        return "linkedin", "Canal sugerido por datos disponibles (LinkedIn)."
    if has_wa:
        return "whatsapp", "Canal sugerido por datos disponibles (WhatsApp)."

    return (
        order[0] if order else "linkedin",
        "Sin datos de contacto claros; se sugiere el primer canal de la campaña para completar datos.",
    )
