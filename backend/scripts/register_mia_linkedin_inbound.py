"""Register Mia LinkedIn inbound for testing."""
from app.database.session import SessionLocal, init_db
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services.linkedin_inbound_sync import register_linkedin_inbound

MIA_MESSAGE = (
    "Hola Joaquin, sí, en SquadS me ocupo de temas de ventas y herramientas del equipo comercial. "
    "Contame un poco más qué hace Nexus y cómo se diferencia de lo que usamos hoy."
)


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        prospect = db.get(Prospect, 10)
        if not prospect:
            print("prospect 10 not found")
            return
        campaign = db.get(Campaign, int(prospect.campaign_id or 0))
        if not campaign:
            print("campaign not found")
            return
        print("before draft:", (prospect.linkedin_assisted_draft or "")[:80])
        result = register_linkedin_inbound(
            db,
            prospect=prospect,
            campaign=campaign,
            message=MIA_MESSAGE,
            linkedin_message_id="test-mia-inbound-1",
            prepare_reply_draft=True,
        )
        db.commit()
        db.refresh(prospect)
        print("result:", result)
        print("after draft:", (prospect.linkedin_assisted_draft or "")[:200])
        print("assist_status:", prospect.linkedin_assist_status)
        print("sequence_paused:", prospect.sequence_paused)
    except Exception as exc:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
