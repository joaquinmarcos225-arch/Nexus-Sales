"""Configura prospecto de prueba con LinkedIn real de Mia Álvarez (familiar)."""

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

from app.database.session import SessionLocal
from app.models.enums import ProspectOwnershipStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.services.linkedin_assisted_service import is_real_linkedin_profile_url

PROSPECT_ID = 10
CAMPAIGN_ID = 4
SELLER_USER_ID = 6
TEST_EMAIL = "joaquinmarcos225@gmail.com"
# Perfil real autorizado por el usuario para prueba LinkedIn
MIA_LINKEDIN = "https://www.linkedin.com/in/mia-%C3%A1lvarez/"
MIA_NAME = "Mia Álvarez"
MIA_COMPANY = "Prueba LinkedIn Nexus"


def main() -> None:
    db = SessionLocal()
    try:
        prospect = db.get(Prospect, PROSPECT_ID)
        user = db.get(User, SELLER_USER_ID)
        if not prospect or not user:
            print("Prospecto o usuario no encontrado")
            return

        linkedin = unquote(MIA_LINKEDIN).rstrip("/")
        if not is_real_linkedin_profile_url(linkedin):
            print("URL LinkedIn no válida:", linkedin)
            return

        backup = (
            f"[TEST Mia LinkedIn] nombre_prev={prospect.name!r} "
            f"linkedin_prev={(prospect.linkedin_url or '')!r} "
            f"email_prev={(prospect.email or '')!r}"
        )
        notes = (prospect.notes or "").strip()
        if backup not in notes:
            prospect.notes = f"{backup}\n{notes}".strip() if notes else backup

        prospect.name = MIA_NAME
        prospect.company_name = MIA_COMPANY
        prospect.linkedin_url = linkedin
        prospect.email = TEST_EMAIL
        prospect.owner_user_id = user.id
        prospect.ownership_status = ProspectOwnershipStatus.tomado.value
        prospect.claimed_at = datetime.now(UTC)

        # Limpiar estado LinkedIn assist para prueba fresca
        prospect.linkedin_assisted_draft = None
        prospect.linkedin_assist_status = None
        prospect.linkedin_assist_session_id = None
        prospect.linkedin_last_assisted_at = None
        prospect.linkedin_sdr_marked_sent_at = None
        prospect.sequence_paused = False

        db.commit()
        db.refresh(prospect)

        log = json.loads(prospect.sequence_touch_log or "{}")
        print("Listo para prueba LinkedIn real")
        print(f"  Prospecto id={prospect.id} -> {prospect.name}")
        print(f"  Empresa: {prospect.company_name}")
        print(f"  Email (solo Gmail): {prospect.email}")
        print(f"  LinkedIn: {prospect.linkedin_url}")
        print(f"  Tomado por: {user.email}")
        print(f"  Secuencia: {prospect.ownership_status} | pausada={prospect.sequence_paused}")
        print("  Toques:", {k: v.get("status") for k, v in sorted(log.items(), key=lambda x: int(x[0]))})
        print()
        print("Pasos:")
        print("  1. Reiniciá backend si hace falta")
        print("  2. Prospectos -> Mia Alvarez -> Ver Secuencia")
        print("  3. Ejecutar toque Dia 4 (LinkedIn) -> Centro outreach -> Enviar mensaje")
        print("  4. Mia te responde en LinkedIn (real)")
        print("  5. En Nexus: Registrar respuesta LinkedIn con su mensaje")
        print("  6. Cola LinkedIn -> Responder en LinkedIn -> Marcar como enviado")
    finally:
        db.close()


if __name__ == "__main__":
    main()
