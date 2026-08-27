"""Re-dispara auto-reply para inbounds que quedaron en skipped_closed (prod)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path("/app/data/nexus_sales.db")


def main() -> None:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    rows = list(
        cur.execute(
            """
            SELECT id, prospect_id, campaign_id, inbound_gmail_message_id, outcome
            FROM inbound_auto_reply_receipts
            WHERE outcome = 'skipped_closed'
            """
        )
    )
    print("BEFORE", json.dumps([dict(r) for r in rows], ensure_ascii=False))
    cur.execute(
        "UPDATE inbound_auto_reply_receipts SET outcome = 'failed' WHERE outcome = 'skipped_closed'"
    )
    con.commit()
    print("updated", cur.rowcount)
    con.close()

    # Re-run via app layer
    from app.database.session import SessionLocal
    from app.models.campaign import Campaign
    from app.models.prospect import Prospect
    from app.services.inbound_auto_reply import ensure_auto_reply_for_gmail_message

    db = SessionLocal()
    try:
        for r in rows:
            prospect = db.get(Prospect, int(r["prospect_id"]))
            campaign = db.get(Campaign, int(r["campaign_id"]))
            if not prospect or not campaign:
                print("missing", dict(r))
                continue
            mid = r["inbound_gmail_message_id"]
            out = ensure_auto_reply_for_gmail_message(
                db,
                campaign=campaign,
                prospect=prospect,
                gmail_message_id=mid,
                prior_prospect_status="contacted",
            )
            db.commit()
            print(
                "REPLAY",
                json.dumps(
                    {
                        "prospect_id": prospect.id,
                        "name": prospect.name,
                        "campaign_id": campaign.id,
                        "seller_id": campaign.seller_id,
                        "mid": mid,
                        "outcome": out,
                    },
                    ensure_ascii=False,
                ),
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
