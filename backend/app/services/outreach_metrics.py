"""Métricas de outreach basadas en mensajes reales en BD (no solo Prospect.status)."""

from __future__ import annotations

import os

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect


def _truthy_env(name: str) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def exclude_testing_messages():
    """Filtra mensajes marcados como simulación / testing."""
    return or_(OutreachMessage.is_testing.is_(False), OutreachMessage.is_testing.is_(None))


def exclude_testing_commercial_prospects():
    """Prospectos cuyo estado comercial proviene solo de simulaciones."""
    return or_(
        Prospect.commercial_state_is_testing.is_(False),
        Prospect.commercial_state_is_testing.is_(None),
    )


def is_real_mode() -> bool:
    """
    Modo solo operación real: sin seed demo, sin simulaciones de outreach/autopilot,
    sin toques automáticos de secuencia 21d en BD, métricas agregadas solo con Gmail real.
    Variable: NEXUS_REAL_MODE=1 (además podés usar NEXUS_DISABLE_OUTREACH_SIMULATION por compatibilidad).
    """
    return _truthy_env("NEXUS_REAL_MODE")


def is_outreach_simulation_disabled() -> bool:
    """Simulaciones de inbound + endpoints simulate-response(s) deshabilitados."""
    if is_real_mode():
        return True
    return _truthy_env("NEXUS_DISABLE_OUTREACH_SIMULATION")


def is_sequence_testing_enabled() -> bool:
    """
    Simulación manual de respuestas en secuencias SDR (POST …/sequence/simulate-response).

    Habilitado si:
    - NEXUS_ENABLE_SEQUENCE_TESTING=1 (explícito; funciona aunque NEXUS_REAL_MODE=1), o
    - simulación general no está deshabilitada.
    """
    if _truthy_env("NEXUS_ENABLE_SEQUENCE_TESTING"):
        return True
    return not is_outreach_simulation_disabled()


def outreach_simulation_config() -> dict[str, str | bool]:
    """Estado auditable de flags de simulación (para health / UI)."""
    real = is_real_mode()
    sim_disabled = is_outreach_simulation_disabled()
    seq_testing = is_sequence_testing_enabled()
    return {
        "real_mode": real,
        "outreach_simulation_disabled": sim_disabled,
        "sequence_testing_enabled": seq_testing,
        "env_nexus_real_mode": (os.getenv("NEXUS_REAL_MODE") or "").strip(),
        "env_nexus_disable_outreach_simulation": (
            os.getenv("NEXUS_DISABLE_OUTREACH_SIMULATION") or ""
        ).strip(),
        "env_nexus_enable_sequence_testing": (
            os.getenv("NEXUS_ENABLE_SEQUENCE_TESTING") or ""
        ).strip(),
        "enable_sequence_testing_hint": (
            "Agregá NEXUS_ENABLE_SEQUENCE_TESTING=1 en backend/.env y reiniciá uvicorn."
            if not seq_testing
            else "Modo testing de secuencias activo."
        ),
        "enable_all_simulation_hint": (
            "Para simulación global: quitá NEXUS_REAL_MODE=1 y no definas "
            "NEXUS_DISABLE_OUTREACH_SIMULATION=1 en backend/.env."
        ),
    }


def distinct_prospects_with_real_gmail_outbound_campaign(db: Session, campaign_id: int) -> int:
    """
    Prospectos contactados de verdad: outbound con gmail_message_id
    (manual user O automatización ai/system).
    """
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id))).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.direction == "outbound",
            OutreachMessage.gmail_message_id.isnot(None),
            OutreachMessage.sender_type.in_(("user", "ai", "system")),
        )
    )
    return int(n or 0)


def distinct_prospects_contacted_campaign(db: Session, campaign_id: int) -> int:
    """
    Contactados = outbound real (cualquier canal).
    No cuenta solo «secuencia iniciada»: LinkedIn/WhatsApp pueden estar
    en cola/verificando sin haber enviado aún (evita progreso falso al 100%).
    """
    from_mail = distinct_prospects_with_real_gmail_outbound_campaign(db, campaign_id)
    from_any = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id))).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.direction == "outbound",
            exclude_testing_messages(),
        )
    )
    return max(int(from_mail or 0), int(from_any or 0))


def distinct_prospects_with_real_gmail_inbound_campaign(db: Session, campaign_id: int) -> int:
    """Prospectos con inbound real (Gmail importado u otro canal)."""
    from sqlalchemy import or_

    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id))).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
            exclude_testing_messages(),
            or_(
                OutreachMessage.gmail_message_id.isnot(None),
                OutreachMessage.channel.in_(("linkedin", "whatsapp", "email")),
            ),
        )
    )
    return int(n or 0)


def distinct_prospects_with_real_gmail_outbound_company(db: Session, company_id: int) -> int:
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.direction == "outbound",
            OutreachMessage.sender_type == "user",
            OutreachMessage.gmail_message_id.isnot(None),
        )
    )
    return int(n or 0)


def distinct_prospects_with_real_gmail_inbound_company(db: Session, company_id: int) -> int:
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
            OutreachMessage.gmail_message_id.isnot(None),
        )
    )
    return int(n or 0)


def distinct_prospects_with_real_gmail_inbound_seller_campaigns(
    db: Session, *, company_id: int, campaign_ids: list[int]
) -> int:
    if not campaign_ids:
        return 0
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            Prospect.campaign_id.in_(campaign_ids),
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
            OutreachMessage.gmail_message_id.isnot(None),
        )
    )
    return int(n or 0)


def distinct_prospects_with_real_gmail_outbound_seller_campaigns(
    db: Session, *, company_id: int, campaign_ids: list[int]
) -> int:
    if not campaign_ids:
        return 0
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            Prospect.campaign_id.in_(campaign_ids),
            OutreachMessage.direction == "outbound",
            OutreachMessage.sender_type == "user",
            OutreachMessage.gmail_message_id.isnot(None),
        )
    )
    return int(n or 0)


def distinct_prospects_with_outbound_campaign(db: Session, campaign_id: int) -> int:
    """Prospectos con al menos un outbound registrado (excluye simulaciones)."""
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id))).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.direction == "outbound",
            OutreachMessage.sender_type.in_(("ai", "system")),
            exclude_testing_messages(),
        )
    )
    return int(n or 0)


def distinct_prospects_with_inbound_campaign(db: Session, campaign_id: int) -> int:
    """Prospectos con al menos un mensaje inbound del prospecto (sin simulaciones)."""
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id))).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
            exclude_testing_messages(),
        )
    )
    return int(n or 0)


def distinct_prospects_with_outbound_company(db: Session, company_id: int) -> int:
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.direction == "outbound",
            OutreachMessage.sender_type.in_(("ai", "system")),
            exclude_testing_messages(),
        )
    )
    return int(n or 0)


def distinct_prospects_with_inbound_company(db: Session, company_id: int) -> int:
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
            exclude_testing_messages(),
        )
    )
    return int(n or 0)


def distinct_prospects_with_outbound_seller_campaigns(
    db: Session, *, company_id: int, campaign_ids: list[int]
) -> int:
    if not campaign_ids:
        return 0
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            Prospect.campaign_id.in_(campaign_ids),
            OutreachMessage.direction == "outbound",
            OutreachMessage.sender_type.in_(("ai", "system")),
            exclude_testing_messages(),
        )
    )
    return int(n or 0)


def distinct_prospects_with_inbound_seller_campaigns(
    db: Session, *, company_id: int, campaign_ids: list[int]
) -> int:
    if not campaign_ids:
        return 0
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            Prospect.campaign_id.in_(campaign_ids),
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
            exclude_testing_messages(),
        )
    )
    return int(n or 0)


def count_outbound_messages_campaign(db: Session, campaign_id: int) -> int:
    """Total de mensajes salientes reales de la campaña (email, LinkedIn, WhatsApp)."""
    if is_real_mode():
        # Email: solo con gmail_message_id (enviados). LinkedIn/WhatsApp: canal asistido.
        from sqlalchemy import and_, or_

        n = db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.direction == "outbound",
                OutreachMessage.sender_type.in_(("user", "ai", "system")),
                exclude_testing_messages(),
                or_(
                    OutreachMessage.gmail_message_id.isnot(None),
                    and_(
                        OutreachMessage.channel.in_(("linkedin", "whatsapp")),
                        OutreachMessage.sender_type.in_(("user", "ai", "system")),
                    ),
                ),
            )
        )
    else:
        n = db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.direction == "outbound",
                OutreachMessage.sender_type.in_(("ai", "system", "user")),
                exclude_testing_messages(),
            )
        )
    return int(n or 0)


def count_inbound_messages_campaign(db: Session, campaign_id: int) -> int:
    """Total de mensajes entrantes del prospecto en la campaña."""
    if is_real_mode():
        from sqlalchemy import or_

        n = db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.direction == "inbound",
                OutreachMessage.sender_type == "prospect",
                exclude_testing_messages(),
                or_(
                    OutreachMessage.gmail_message_id.isnot(None),
                    OutreachMessage.channel.in_(("linkedin", "whatsapp", "email")),
                ),
            )
        )
    else:
        n = db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.direction == "inbound",
                OutreachMessage.sender_type == "prospect",
                exclude_testing_messages(),
            )
        )
    return int(n or 0)


def count_prospects_by_status_company(
    db: Session,
    company_id: int,
    status: str,
    *,
    include_testing: bool = False,
) -> int:
    q = select(func.count(Prospect.id)).where(
        Prospect.company_id == company_id,
        Prospect.status == status,
    )
    if not include_testing:
        q = q.where(exclude_testing_commercial_prospects())
    return int(db.scalar(q) or 0)
