"""Lista de chats de WhatsApp Web que la extensión vigila para leer respuestas.

Sale del servidor (no del navegador): prospectos a los que la empresa les escribió por
WhatsApp en los últimos días. La extensión matchea chats sólo por teléfono.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.whatsapp_cloud_service import meta_api_recipient_candidates

WATCH_DAYS = 30
WATCH_LIMIT = 200
# Solo desfase de relojes: la extensión marca el envío al apretar Enter. Un margen más amplio
# tomaba como respuesta mensajes que el prospecto mandó justo antes del nuestro.
SINCE_MARGIN = timedelta(seconds=10)


def phone_variants(phone: str | None, whatsapp: str | None) -> list[str]:
    out = list(meta_api_recipient_candidates(phone, whatsapp))
    for d in list(out):
        mx = re.match(r"^52(1?)(\d{10})$", d)
        if mx:
            alt = f"52{mx.group(2)}" if mx.group(1) else f"521{mx.group(2)}"
            if alt not in out:
                out.append(alt)
    return out


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def whatsapp_reading_allowed(prospect: Prospect, campaign: Campaign | None) -> bool:
    """
    Sólo se leen chats de secuencias vivas: campaña corriendo, secuencia iniciada, sin pausa
    manual del SDR, no encajonada y no terminada (reunión confirmada o ya pasada).
    La pausa automática por respuesta del prospecto no corta la lectura: la charla sigue.
    """
    from app.models.enums import CampaignStatus
    from app.services import meeting_scheduling_extras as extras
    from app.services.multichannel_sequence import SEQUENCE_GROUP_ENCAJONADO

    if campaign is None:
        return False
    if (campaign.status or "").lower() != CampaignStatus.running.value or getattr(
        campaign, "automation_paused", False
    ):
        return False
    if getattr(prospect, "sequence_started_at", None) is None:
        return False
    if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_ENCAJONADO:
        return False
    if extras.should_ignore_inbound_after_meeting(prospect):
        return False
    state = extras._state(prospect)
    return not state.get("manual_pause") and not state.get("meeting_done_id")


def whatsapp_watch_list(db: Session, company_id: int, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=WATCH_DAYS)
    rows = db.execute(
        select(
            OutreachMessage.prospect_id,
            func.min(OutreachMessage.created_at),
            func.max(OutreachMessage.created_at),
        )
        .join(Prospect, Prospect.id == OutreachMessage.prospect_id)
        .where(
            Prospect.company_id == int(company_id),
            Prospect.campaign_id.is_not(None),
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "outbound",
            OutreachMessage.is_testing.is_(False),
            OutreachMessage.created_at >= cutoff,
        )
        .group_by(OutreachMessage.prospect_id)
        .order_by(func.max(OutreachMessage.created_at).desc())
        .limit(WATCH_LIMIT)
    ).all()
    if not rows:
        return []
    prospects = {
        p.id: p for p in db.scalars(select(Prospect).where(Prospect.id.in_([r[0] for r in rows]))).all()
    }
    campaigns = {
        c.id: c
        for c in db.scalars(
            select(Campaign).where(Campaign.id.in_({p.campaign_id for p in prospects.values() if p.campaign_id}))
        ).all()
    }
    taken: set[str] = set()
    out: list[dict[str, Any]] = []
    for prospect_id, first_out, _last_out in rows:
        p = prospects.get(prospect_id)
        if p is None or first_out is None:
            continue
        if not whatsapp_reading_allowed(p, campaigns.get(p.campaign_id)):
            continue
        phones = phone_variants(p.phone, p.whatsapp)
        # Mismo teléfono en dos prospectos: gana el de envío más reciente (rows viene ordenado).
        if not phones or any(ph in taken for ph in phones):
            continue
        taken.update(phones)
        out.append(
            {
                "prospect_id": int(p.id),
                "name": p.name or "",
                "phones": phones,
                "since": int((_as_utc(first_out) - SINCE_MARGIN).timestamp()),
            }
        )
    return out
