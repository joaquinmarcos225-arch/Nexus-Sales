"""Prepara prospecto para probar los 7 toques del playbook (campaña 4)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.enums import ProspectOwnershipStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK
from app.services.prospect_sequence import generate_sequence_preview

# Prospecto real de campaña 4 con email + LinkedIn
PROSPECT_ID = 10
CAMPAIGN_ID = 4
# Usuario vendedor de la campaña (SDR Test)
SELLER_USER_ID = 6
# Teléfono ficticio para habilitar WhatsApp Día 7 y 16 en la preview
TEST_WHATSAPP = "+5491112345678"


def main() -> None:
    db = SessionLocal()
    try:
        user = db.get(User, SELLER_USER_ID)
        if user is None:
            print("Usuario vendedor no encontrado:", SELLER_USER_ID)
            return

        prospect = db.get(Prospect, PROSPECT_ID)
        campaign = db.scalars(
            select(Campaign).where(Campaign.id == CAMPAIGN_ID).options(selectinload(Campaign.product))
        ).first()
        if not prospect or not campaign:
            print("Prospecto o campaña no encontrados")
            return

        prospect.phone = TEST_WHATSAPP
        prospect.whatsapp = TEST_WHATSAPP
        prospect.owner_user_id = user.id
        prospect.ownership_status = ProspectOwnershipStatus.tomado.value
        prospect.claimed_at = datetime.now(UTC)
        prospect.ownership_cooldown_until = None
        db.commit()
        db.refresh(prospect)

        print(f"Preparado: {prospect.name} (id={prospect.id})")
        print(f"  Campaña: {campaign.name} (id={campaign.id})")
        print(f"  Email: {prospect.email}")
        print(f"  LinkedIn: {(prospect.linkedin_url or '')[:60]}...")
        print(f"  WhatsApp: {prospect.whatsapp}")
        print(f"  Tomado por: {user.email}")
        print()
        print("Generando preview de los 7 toques (puede tardar ~1 min)...")

        result = generate_sequence_preview(
            db,
            user=user,
            prospect=prospect,
            force_regenerate=True,
        )
        db.commit()
        db.refresh(prospect)

        touches = result.get("touches") or []
        print(f"\nListo: {len(touches)} toques guardados en sequence_playbook_draft")
        print(f"Playbook: {result.get('playbook_name')}")
        print()
        for t in touches:
            day = t.get("day")
            ch = t.get("channel")
            obj = (t.get("objective") or "")[:90]
            preview = (t.get("body_preview") or "").replace("\n", " ")[:100]
            print(f"  Día {day} · {ch}: {obj}...")
            print(f"    > {preview}...")
        print()
        print("--- Cómo probar en la UI ---")
        print("1. Iniciá sesión como SDR Test (sdr@test.com)")
        print("2. Prospectos → abrí Gastón Lucena")
        print("3. Panel Outreach → verás los 7 toques en vista previa")
        print("4. O: Campaña Outbound LATAM Q1.2 → pestaña Sourcing → workspace outreach")
        print(f"5. Prospect id para API: {prospect.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
