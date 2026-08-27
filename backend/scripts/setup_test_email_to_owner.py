"""Prueba de email de secuencia al mail del SDR; LinkedIn queda el perfil real."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from app.database.session import SessionLocal
from app.models.prospect import Prospect
from app.services.prospect_sequence import (
    PLAYBOOK_DAYS,
    TOUCH_PENDIENTE,
    _draft_by_day,
    _fired_list,
    _remove_fired,
    _save_touch_log,
    _set_touch_entry,
    _touch_log,
    generate_sequence_preview,
)
from app.models.user import User

PROSPECT_ID = 10
SELLER_USER_ID = 6
# Solo para prueba de borradores/envío Gmail — LinkedIn no se toca
TEST_EMAIL_TO = "joaquinmarcos225@gmail.com"
RESET_DAYS = (1,)  # reintentar Día 1 con tu mail


def main() -> None:
    db = SessionLocal()
    try:
        prospect = db.get(Prospect, PROSPECT_ID)
        user = db.get(User, SELLER_USER_ID)
        if not prospect or not user:
            print("Prospecto o usuario no encontrado")
            return

        original_email = (prospect.email or "").strip()
        linkedin = (prospect.linkedin_url or "").strip()
        note_tag = f"[TEST email SDR] email real: {original_email}"
        notes = (prospect.notes or "").strip()
        if original_email and original_email.lower() != TEST_EMAIL_TO.lower():
            if note_tag not in notes:
                prospect.notes = f"{note_tag}\n{notes}".strip() if notes else note_tag

        prospect.email = TEST_EMAIL_TO
        db.commit()
        db.refresh(prospect)

        log = _touch_log(prospect)
        for day in RESET_DAYS:
            if str(day) in log:
                _remove_fired(prospect, day)
                _set_touch_entry(
                    prospect,
                    day,
                    status=TOUCH_PENDIENTE,
                    sent_at=None,
                    message_id=None,
                    error=None,
                    validation_rejection=None,
                    openai_last_error=None,
                    generation_context=None,
                    fallback_test=False,
                    whatsapp_message_id=None,
                    gmail_message_id=None,
                    gmail_draft_id=None,
                    gmail_web_link=None,
                    gmail_manually_sent=None,
                )
        if prospect.sequence_started_at and not _fired_list(prospect):
            pass
        db.commit()

        print("Listo para probar email con tu cuenta")
        print(f"  Prospecto: {prospect.name} (id={prospect.id})")
        print(f"  Email to (prueba): {prospect.email}")
        print(f"  Email real (en notas): {original_email or '(sin guardar)'}")
        print(f"  LinkedIn (sin cambios): {linkedin[:70]}...")
        print(f"  Toques reseteados: {list(RESET_DAYS)}")
        print()
        print("Pasos:")
        print("  1. Reiniciá backend si no recargó el cambio de borradores")
        print("  2. Prospectos -> Gaston -> Ver Secuencia")
        print("  3. Ejecutar toque Dia 1 -> borrador en Gmail")
        print("  4. Enviar desde Gmail -> Marcar como enviado en Nexus")
        print("  5. Dia 4 LinkedIn sigue con el perfil real de sourcing")
    finally:
        db.close()


if __name__ == "__main__":
    main()
