"""Smoke: resolve + register inbound (con borrador de réplica, como la extensión)."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import unquote

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.user import User
from app.services.linkedin_assisted_service import resolve_prospect_by_linkedin_url
from app.services.linkedin_inbound_sync import register_linkedin_inbound
from app.services.whatsapp_inbound_sync import (
    register_whatsapp_inbound,
    resolve_prospect_by_whatsapp_digits,
)


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "director@test.com"))
        assert user and user.company_id
        company_id = int(user.company_id)
        stamp = time.time_ns()

        url = unquote("https://www.linkedin.com/in/mia-%C3%A1lvarez/").rstrip("/")
        li = resolve_prospect_by_linkedin_url(db, company_id=company_id, url=url)
        print(f"resolve LI -> id={getattr(li, 'id', None)} name={getattr(li, 'name', None)}")

        wa = resolve_prospect_by_whatsapp_digits(
            db, company_id=company_id, from_digits="5491128942875"
        )
        print(
            f"resolve WA -> id={getattr(wa, 'id', None)} "
            f"name={getattr(wa, 'name', None)} phone={getattr(wa, 'phone', None)}"
        )

        if li:
            camp = db.get(Campaign, li.campaign_id)
            out = register_linkedin_inbound(
                db,
                prospect=li,
                campaign=camp,
                message=f"Hola, me interesa. ¿Podemos hablar esta semana? (prep {stamp})",
                linkedin_message_id=f"prep-reg-li-{stamp}",
            )
            db.commit()
            print("LI register:", {k: out.get(k) for k in out if k != "reply_draft"})
            print("  draft head:", (out.get("reply_draft") or "")[:140])

        if wa:
            camp = db.get(Campaign, wa.campaign_id)
            out = register_whatsapp_inbound(
                db,
                prospect=wa,
                campaign=camp,
                message=f"Hola, vi el mensaje. Contame más (prep {stamp})",
                whatsapp_message_id=f"prep-reg-wa-{stamp}",
            )
            db.commit()
            print("WA register:", {k: out.get(k) for k in out if k != "reply_draft"})
            print("  draft head:", (out.get("reply_draft") or "")[:140])

        print("OK — colas deberían mostrar réplicas listas")
    finally:
        db.close()


if __name__ == "__main__":
    main()
