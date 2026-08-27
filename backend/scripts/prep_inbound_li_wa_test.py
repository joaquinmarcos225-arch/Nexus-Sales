"""Prep rápida para prueba inbound LinkedIn + WhatsApp (demo)."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from sqlalchemy import select, text

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.enums import ProspectOwnershipStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.services.linkedin_assisted_service import is_real_linkedin_profile_url

# Mia (LinkedIn real autorizado) — campaña 4
MIA_PROSPECT_ID = 10
MIA_CAMPAIGN_ID = 4
MIA_LINKEDIN = "https://www.linkedin.com/in/mia-%C3%A1lvarez/"
MIA_NAME = "Mia Álvarez"

# WhatsApp demo phone used in setup scripts
DEMO_WA_PHONE = "+5491128942875"


def _user_by_email(db, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def main() -> None:
    db = SessionLocal()
    try:
        director = _user_by_email(db, "director@test.com")
        sdr = _user_by_email(db, "sdr@test.com")
        print("=== Usuarios ===")
        print(f"  director@test.com id={getattr(director, 'id', None)}")
        print(f"  sdr@test.com id={getattr(sdr, 'id', None)}")

        seller = director or sdr
        if not seller:
            print("ERROR: no hay usuario demo")
            return

        # Campañas: seller = director (tiene Google en demos recientes)
        camps = list(db.scalars(select(Campaign).order_by(Campaign.id)))
        print("=== Campañas (seller) ===")
        for c in camps:
            if c.seller_id != seller.id and c.id in (3, 4, 5):
                prev = c.seller_id
                c.seller_id = seller.id
                print(f"  campaign {c.id} seller {prev} -> {seller.id} ({c.name})")
            else:
                print(f"  campaign {c.id} seller={c.seller_id} name={c.name!r}")

        # --- LinkedIn: Mia ---
        p = db.get(Prospect, MIA_PROSPECT_ID)
        if p:
            linkedin = unquote(MIA_LINKEDIN).rstrip("/")
            ok = is_real_linkedin_profile_url(linkedin)
            p.name = MIA_NAME
            p.linkedin_url = linkedin
            p.owner_user_id = seller.id
            p.ownership_status = ProspectOwnershipStatus.tomado.value
            p.claimed_at = datetime.now(UTC)
            p.sequence_paused = False
            p.linkedin_assisted_draft = None
            p.linkedin_assist_status = None
            p.linkedin_assist_session_id = None
            p.linkedin_reply_available_at = None
            print("=== LinkedIn (Mia) ===")
            print(f"  prospect id={p.id} campaign={p.campaign_id} linkedin_ok={ok}")
            print(f"  url={p.linkedin_url}")
        else:
            print("=== LinkedIn: prospect 10 no existe ===")

        # --- WhatsApp: primer prospecto con ese phone, o Mia si no hay ---
        wa = db.scalar(
            select(Prospect).where(Prospect.phone.ilike(f"%{DEMO_WA_PHONE[-10:]}%")).limit(1)
        )
        if not wa and p:
            # Adjuntar phone de prueba a Mia para un solo contacto dual-canal si hace falta
            # Mejor: buscar cualquier prospect con phone real
            wa = db.scalar(
                select(Prospect)
                .where(Prospect.phone.isnot(None), Prospect.phone != "")
                .order_by(Prospect.id.desc())
                .limit(1)
            )
        print("=== WhatsApp ===")
        if wa:
            if not (wa.phone or "").strip():
                wa.phone = DEMO_WA_PHONE
            wa.owner_user_id = seller.id
            wa.ownership_status = ProspectOwnershipStatus.tomado.value
            wa.claimed_at = datetime.now(UTC)
            wa.sequence_paused = False
            wa.whatsapp_assisted_draft = None
            wa.whatsapp_assist_status = None
            print(f"  prospect id={wa.id} name={wa.name!r} phone={wa.phone!r} campaign={wa.campaign_id}")
        else:
            print("  sin prospecto con phone — cargá un número real en un prospecto antes de probar")

        db.commit()

        # Health snapshot
        print("=== Env delay LI ===")
        import os

        print(f"  NEXUS_LINKEDIN_REPLY_DELAY_MINUTES={os.getenv('NEXUS_LINKEDIN_REPLY_DELAY_MINUTES', '2 (default)')}")
        print("OK prep listo")
    finally:
        db.close()


if __name__ == "__main__":
    main()
