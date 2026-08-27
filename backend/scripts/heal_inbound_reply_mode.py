"""Asegura campañas con inbound auto_send + delay 2 min."""
from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.campaign import Campaign


def main() -> None:
    db = SessionLocal()
    try:
        rows = list(db.scalars(select(Campaign)).all())
        n = 0
        for c in rows:
            changed = False
            if (c.inbound_reply_mode or "").strip() != "auto_send":
                c.inbound_reply_mode = "auto_send"
                changed = True
            delay = getattr(c, "inbound_reply_delay_minutes", None)
            if delay is None or int(delay) < 1:
                if hasattr(c, "inbound_reply_delay_minutes"):
                    c.inbound_reply_delay_minutes = 2
                    changed = True
            if changed:
                n += 1
        db.commit()
        print(f"campaigns_updated={n} total={len(rows)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
