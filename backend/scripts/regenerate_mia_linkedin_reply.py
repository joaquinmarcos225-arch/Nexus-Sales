"""Regenera borrador de réplica LinkedIn para Mia (prospect 10)."""
from app.database.session import SessionLocal, init_db
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services import linkedin_assisted_service
from sqlalchemy.orm import selectinload
from sqlalchemy import select


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        prospect = db.get(Prospect, 10)
        if not prospect:
            print("prospect 10 not found")
            return
        campaign = db.scalars(
            select(Campaign)
            .where(Campaign.id == int(prospect.campaign_id))
            .options(selectinload(Campaign.product), selectinload(Campaign.seller))
        ).first()
        if not campaign:
            print("campaign not found")
            return
        draft = linkedin_assisted_service.regenerate_linkedin_reply_draft(
            db, prospect, campaign
        )
        db.commit()
        print("regenerated draft:\n")
        print(draft)
    finally:
        db.close()


if __name__ == "__main__":
    main()
