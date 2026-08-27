"""Repara Ivan (prospect 26): visible en cola + mensaje regenerado con investigación."""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.product import Product
from app.models.prospect import Prospect
from app.services import linkedin_assisted_service as las
from app.services.outreach_prospect_research import ensure_outreach_research
from app.services.prospect_sequence import _generate_real_touch_content


def main() -> None:
    db = SessionLocal()
    try:
        p = db.get(Prospect, 26)
        if not p:
            print("prospect 26 missing")
            return
        camp = db.get(Campaign, p.campaign_id)
        product = db.get(Product, camp.product_id) if camp and camp.product_id else None

        brief = ensure_outreach_research(
            db, prospect=p, campaign=camp, product=product, force=True
        )
        print("research:", brief[:400])

        content = _generate_real_touch_content(
            db,
            prospect=p,
            campaign=camp,
            product=product,
            day=1,
            prior=[],
        )
        body = content["body"]
        print("new draft:", body[:400])

        # Visible: Conectar (si no hay reporte de 1º grado).
        las.mark_connect_suggested(
            db, p, camp, pending_draft=body, log_event=True
        )
        # Sync touch log body
        from app.services.prospect_sequence import _touch_log, _save_touch_log, TOUCH_GENERADO

        log = _touch_log(p)
        log["1"] = {
            **(log.get("1") or {}),
            "status": TOUCH_GENERADO,
            "message_body": body,
            "body": body,
            "error": None,
        }
        _save_touch_log(p, log)
        db.commit()
        print(
            "OK Ivan visible. conn=",
            p.linkedin_connection_status,
            "assist=",
            p.linkedin_assist_status,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
