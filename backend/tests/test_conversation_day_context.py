from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.meeting_booking import _conversation_day_context
from app.services.outreach_simulation import make_message


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_conversation_day_context_ignores_wrong_confirmation():
    db = _session()
    prospect = Prospect(
        id=1,
        company_id=1,
        campaign_id=1,
        name="Test",
        company_name="Co",
        email="p@test.com",
        status="interested",
        compatibility_score=80,
        interest_probability=50,
    )
    db.add(prospect)
    db.flush()

    db.add(
        make_message(
            prospect_id=prospect.id,
            campaign_id=1,
            sender_type="user",
            message=(
                "[Gmail · envío real]\nAsunto: Re: reunión\n\n"
                "Listo, moví la reunión para jueves a las 14:30."
            ),
            channel="email",
            direction="outbound",
        )
    )
    db.add(
        make_message(
            prospect_id=prospect.id,
            campaign_id=1,
            sender_type="user",
            message=(
                "[Gmail · envío real]\nAsunto: Re: horarios\n\n"
                "El viernes que mencionaste no tengo a las 15:00, "
                "pero sí a las 14:30, 15:30, 14:00.\n\n¿Te sirve alguno?"
            ),
            channel="email",
            direction="outbound",
        )
    )
    db.add(
        make_message(
            prospect_id=prospect.id,
            campaign_id=1,
            sender_type="prospect",
            message="Hola, me interesa. ¿Podemos hablar el viernes a las 15 hs?",
            channel="email",
            direction="inbound",
        )
    )
    db.commit()

    ctx = _conversation_day_context(
        db,
        prospect,
        timezone="America/Argentina/Buenos_Aires",
        current_inbound="A las 14:30 puedo 15 min, me agendas porfavor?",
    )
    assert ctx is not None
    from zoneinfo import ZoneInfo

    local = ctx.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.weekday() == 4  # viernes
