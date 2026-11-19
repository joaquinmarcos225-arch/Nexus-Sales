"""Autorización estricta de contactos para WhatsApp nativo (Meow).

No reutiliza matching difuso de la extensión/API: solo coincidencia exacta de dígitos
normalizados con phone/whatsapp del prospecto, misma empresa y contexto comercial válido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.models.whatsapp_meow import WhatsappMeowSession
from app.services import prospect_opt_out
from app.services.whatsapp_cloud_service import normalize_whatsapp_digits
from app.services.whatsapp_meow_commercial_isolation import peer_blocks_commercial


def peer_to_digits(peer: str) -> str:
    """Extrae dígitos del JID o teléfono entrante.

    WhatsApp a veces manda device JID `54911…:1@s.whatsapp.net` — hay que quedarse
    con la parte de usuario (antes de `:`)`, no con el id de dispositivo.
    """
    raw = (peer or "").strip()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    return re.sub(r"\D", "", raw)


def _prospect_digits_exact(prospect: Prospect) -> set[str]:
    out: set[str] = set()
    for field in (prospect.whatsapp, prospect.phone):
        norm = normalize_whatsapp_digits(field, field)
        if norm and len(norm) >= 8:
            out.add(norm)
    return out


def _is_lid_peer(peer: str) -> bool:
    return "@lid" in (peer or "").strip().lower()


def _commercial_lid(prospect: Prospect) -> str:
    from app.services.commercial_conversation_state import _commercial

    return str((_commercial(prospect).get("whatsapp_lid_peer") or "")).strip().lower()


def remember_whatsapp_lid_peer(prospect: Prospect, peer: str) -> None:
    """Guarda JID @lid de *este* prospecto (como thread-id de Gmail)."""
    if not _is_lid_peer(peer):
        return
    from app.services.commercial_conversation_state import _commercial, _save_commercial

    block = _commercial(prospect)
    block["whatsapp_lid_peer"] = (peer or "").strip()
    _save_commercial(prospect, block)


def find_prospect_by_stored_lid(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    lid_peer: str,
) -> Prospect | None:
    lid = (lid_peer or "").strip().lower()
    if not lid or not _is_lid_peer(lid):
        return None
    rows = db.scalars(
        select(Prospect)
        .where(Prospect.company_id == int(company_id))
        .order_by(Prospect.id.desc())
        .limit(2000)
    ).all()
    hits = [p for p in rows if _commercial_lid(p) == lid]
    return _pick_unique_seller_prospect(db, hits, seller_user_id=seller_user_id)


def _pick_unique_seller_prospect(
    db: Session,
    candidates: list[Prospect],
    *,
    seller_user_id: int,
) -> Prospect | None:
    """Un teléfono / un hilo. Si hay dos fichas vivas del mismo vendedor → None (ambiguo)."""
    from app.services.inbound_reply_limits import leftover_qa_whatsapp_prospect

    if not candidates:
        return None
    live: list[Prospect] = []
    for p in candidates:
        if not getattr(p, "sequence_started_at", None):
            continue
        if not p.campaign_id:
            continue
        camp = db.get(Campaign, p.campaign_id)
        if camp is None:
            continue
        if int(camp.seller_id or 0) != int(seller_user_id):
            continue
        if leftover_qa_whatsapp_prospect(p):
            continue
        live.append(p)
    if len(live) == 1:
        return live[0]
    if len(live) > 1:
        return None
    rest = [
        p
        for p in candidates
        if getattr(p, "campaign_id", None) and not leftover_qa_whatsapp_prospect(p)
    ]
    if len(rest) == 1:
        return rest[0]
    return None


def find_prospect_for_meow_inbound(
    db: Session,
    *,
    company_id: int,
    session_uuid: str,
    from_peer: str,
    seller_user_id: int | None = None,
) -> Prospect | None:
    """Match inbound Meow → un solo prospecto (dígitos exactos o LID ya vinculado)."""
    del session_uuid
    if peer_blocks_commercial(from_peer):
        return None
    peer = (from_peer or "").strip()
    seller_id = int(seller_user_id or 0)
    if _is_lid_peer(peer):
        # Nunca adivinar por «último envío de la sesión» (mezcla chats).
        return find_prospect_by_stored_lid(
            db, company_id=company_id, seller_user_id=seller_id, lid_peer=peer
        )
    digits = peer_to_digits(peer)
    if len(digits) < 8:
        return None
    return find_prospect_exact_whatsapp_match(
        db, company_id=company_id, from_digits=digits, seller_user_id=seller_id
    )


def find_prospect_exact_whatsapp_match(
    db: Session,
    *,
    company_id: int,
    from_digits: str,
    seller_user_id: int = 0,
) -> Prospect | None:
    """Prospecto con teléfono idéntico. Si hay dos fichas vivas del vendedor → None."""
    digits = re.sub(r"\D", "", from_digits or "")
    if len(digits) < 8:
        return None
    rows = db.scalars(
        select(Prospect)
        .where(Prospect.company_id == int(company_id))
        .order_by(Prospect.id.desc())
        .limit(2000)
    ).all()
    hits = [p for p in rows if digits in _prospect_digits_exact(p)]
    return _pick_unique_seller_prospect(db, hits, seller_user_id=seller_user_id)


@dataclass(frozen=True)
class MeowInboundAuthorization:
    allowed: bool
    reason: str
    prospect: Prospect | None = None
    campaign: Campaign | None = None


def authorize_meow_inbound_commercial(
    db: Session,
    *,
    session_row: WhatsappMeowSession,
    from_peer: str,
    body: str,
) -> MeowInboundAuthorization:
    """Decide si el inbound puede disparar secuencia, conversación y auto-respuesta."""
    block = peer_blocks_commercial(from_peer)
    if block:
        return MeowInboundAuthorization(False, block)
    _ = (body or "").strip()
    prospect = find_prospect_for_meow_inbound(
        db,
        company_id=int(session_row.company_id),
        session_uuid=session_row.session_uuid,
        from_peer=from_peer,
        seller_user_id=int(session_row.user_id),
    )
    if prospect is None:
        digits = peer_to_digits(from_peer)
        if not _is_lid_peer(from_peer) and len(digits) < 8:
            return MeowInboundAuthorization(False, "invalid_sender")
        hits = []
        if digits and len(digits) >= 8:
            rows = db.scalars(
                select(Prospect).where(Prospect.company_id == int(session_row.company_id)).limit(2000)
            ).all()
            hits = [p for p in rows if digits in _prospect_digits_exact(p)]
        if len(hits) > 1:
            return MeowInboundAuthorization(False, "ambiguous_contact")
        return MeowInboundAuthorization(False, "unknown_contact")

    if int(prospect.company_id) != int(session_row.company_id):
        return MeowInboundAuthorization(False, "company_mismatch", prospect=prospect)

    campaign = db.get(Campaign, prospect.campaign_id) if prospect.campaign_id else None
    if campaign is None:
        return MeowInboundAuthorization(False, "no_campaign", prospect=prospect)

    if int(campaign.seller_id or 0) != int(session_row.user_id):
        return MeowInboundAuthorization(
            False,
            "seller_session_mismatch",
            prospect=prospect,
            campaign=campaign,
        )

    if prospect_opt_out.is_suppressed(prospect):
        return MeowInboundAuthorization(
            False,
            "opt_out",
            prospect=prospect,
            campaign=campaign,
        )

    if not getattr(prospect, "sequence_started_at", None):
        return MeowInboundAuthorization(
            False,
            "no_active_sequence",
            prospect=prospect,
            campaign=campaign,
        )

    # sequence_paused (por inbound) frena toques del playbook; la IA puede seguir conversando.
    # Reunión agendada / ai_paused → no mirar más este chat.
    # manual_pause (Pausar / Handoff) se autoriza para LOGUEAR inbound, pero auto-reply se corta
    # en deliver_whatsapp_auto_reply_for_inbound.
    from app.services.meeting_scheduling_extras import auto_reply_silenced

    silence = auto_reply_silenced(prospect)
    if silence in ("meeting_booked", "meeting_done", "ai_paused"):
        return MeowInboundAuthorization(
            False,
            silence,
            prospect=prospect,
            campaign=campaign,
        )

    if _is_lid_peer(from_peer):
        remember_whatsapp_lid_peer(prospect, from_peer)

    return MeowInboundAuthorization(
        True,
        "ok",
        prospect=prospect,
        campaign=campaign,
    )
