"""
Outreach simulado de Nexus Sales.

Preparado para integrar proveedores reales luego (OpenAI, LinkedIn, Gmail, WhatsApp):
- hoy usa plantillas dinámicas simples
- conserva modelo de mensajes/conversación para enchufar proveedores sin romper contratos API
"""

from __future__ import annotations

import random

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect


PRIORITY_CHANNELS = ("linkedin", "email", "whatsapp")


def choose_channel(prospect: Prospect, allowed: list[str] | None = None) -> str:
    """
    Prioriza linkedin → email → whatsapp entre los canales habilitados en la campaña.
    Si falta contacto puntual pero el canal está permitido, se usa igual (envío simulado).
    """
    allowed_ordered = []
    seen: set[str] = set()
    if allowed:
        for ch in PRIORITY_CHANNELS:
            if ch in allowed and ch not in seen:
                allowed_ordered.append(ch)
                seen.add(ch)
        for ch in allowed:
            if ch not in seen:
                allowed_ordered.append(ch)
                seen.add(ch)
    if not allowed_ordered:
        allowed_ordered = list(PRIORITY_CHANNELS)

    for ch in allowed_ordered:
        if ch == "linkedin" and prospect.linkedin_url:
            return ch
        if ch == "email" and prospect.email:
            return ch
        if ch == "whatsapp" and prospect.phone:
            return ch
    # Sin señales de contacto: primer canal permitido para simular entrega igual
    return allowed_ordered[0]


def build_outbound_message(campaign: Campaign, prospect: Prospect) -> str:
    name = (prospect.name or "equipo").split()[0]
    company_name = prospect.company_name or "tu empresa"
    industry = prospect.industry or campaign.target_industry or "tu sector"
    product = campaign.product.name if campaign.product else "nuestra solución"
    return (
        f"Hola {name}, vi que trabajás en {company_name} dentro del sector {industry}. "
        f"Estoy contactándote porque {product} ayuda a equipos como el tuyo a ordenar pipeline, "
        "priorizar prospectos y mejorar la conversión comercial. "
        "¿Te comparto un resumen corto para evaluar fit?"
    )


def build_response_message(status: ProspectStatus, prospect: Prospect) -> str:
    first_name = (prospect.name or "Hola").split()[0]
    if status == ProspectStatus.interested:
        return (
            f"Hola, gracias. Sí {first_name}, me interesa revisar esto. "
            "¿Podemos coordinar una reunión breve esta semana?"
        )
    if status == ProspectStatus.replied:
        return "Gracias por escribir. Lo reviso con el equipo y te respondo en breve."
    if status == ProspectStatus.not_interested:
        return "Gracias, por ahora no estamos evaluando este tipo de solución."
    return "No pudimos continuar esta conversación en este momento."


def build_followup_meeting_suggestion(campaign: Campaign) -> str:
    return (
        "Excelente, te propongo una demo de 20 minutos para revisar casos y próximos pasos. "
        f"Podés tomar horario desde este link: {campaign.calendar_link}"
    )


def roll_response_status(prospect: Prospect) -> ProspectStatus:
    # Sesgo leve por compatibilidad para que la simulación tenga coherencia.
    score = int(prospect.compatibility_score or 0)
    if score >= 80:
        population = [
            ProspectStatus.interested,
            ProspectStatus.replied,
            ProspectStatus.not_interested,
            ProspectStatus.failed,
        ]
        weights = [0.42, 0.34, 0.18, 0.06]
    elif score >= 60:
        population = [
            ProspectStatus.replied,
            ProspectStatus.interested,
            ProspectStatus.not_interested,
            ProspectStatus.failed,
        ]
        weights = [0.4, 0.26, 0.24, 0.1]
    else:
        population = [
            ProspectStatus.not_interested,
            ProspectStatus.replied,
            ProspectStatus.failed,
            ProspectStatus.interested,
        ]
        weights = [0.4, 0.28, 0.24, 0.08]
    return random.choices(population=population, weights=weights, k=1)[0]


def make_message(
    *,
    prospect_id: int,
    campaign_id: int,
    sender_type: str,
    message: str,
    channel: str,
    direction: str,
    gmail_message_id: str | None = None,
    is_testing: bool = False,
) -> OutreachMessage:
    return OutreachMessage(
        prospect_id=prospect_id,
        campaign_id=campaign_id,
        sender_type=sender_type,
        message=message,
        channel=channel,
        direction=direction,
        gmail_message_id=gmail_message_id,
        is_testing=bool(is_testing),
    )
