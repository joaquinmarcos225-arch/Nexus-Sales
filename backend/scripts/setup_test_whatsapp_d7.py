"""Prepara prospecto Test WhatsApp D7 en campaña 3 (listo para ejecutar Día 7)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.enums import ProspectOwnershipStatus, ProspectStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.services import prospect_ownership as own
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK
from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_OMITIDO,
    TOUCH_PENDIENTE,
    _completed_days,
    _draft_by_day,
    _save_touch_log,
    _touch_log,
    execute_sequence_touch,
    next_executable_day,
    start_prospect_sequence,
)

DEMO_PHONE = "+5491128942875"
DEMO_EMAIL = "fernandezjoaquinjose+wa7@gmail.com"


def _ensure_owner(db, *, user: User, prospect: Prospect) -> None:
    if prospect.owner_user_id == user.id:
        return
    prospect.owner_user_id = user.id
    prospect.claimed_at = datetime.now(UTC)
    prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    prospect.ownership_cooldown_until = None
    db.commit()
    db.refresh(prospect)


def _reset_to_day7_ready(prospect: Prospect) -> None:
    now = datetime.now(UTC).isoformat()
    log = _touch_log(prospect)
    draft = _draft_by_day(prospect)

    day1_body = (
        (log.get("1") or {}).get("message_body")
        or (draft.get(1) or {}).get("body")
        or "Email Día 1 enviado (test WhatsApp)."
    )
    log["1"] = {
        "status": TOUCH_ENVIADO,
        "sent_at": now,
        "message_body": day1_body,
        "body": day1_body,
    }
    log["4"] = {
        "status": TOUCH_OMITIDO,
        "skipped_at": now,
    }

    for step in DEFAULT_MVP_PLAYBOOK:
        if step.day in (1, 4):
            continue
        log[str(step.day)] = {"status": TOUCH_PENDIENTE}

    log["7"] = {
        "status": TOUCH_PENDIENTE,
        "error": None,
        "validation_rejection": None,
        "body": None,
        "message_body": None,
        "subject": None,
        "sent_at": None,
        "message_id": None,
        "fallback_test": False,
    }
    _save_touch_log(prospect, log)
    prospect.sequence_fired_milestones = json.dumps([1])
    prospect.sequence_paused = False
    prospect.sequence_completed_at = None
    prospect.sequence_group = "contactado"
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_status = None
    from app.services.prospect_sequence import compute_next_touch

    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalars(select(User).where(User.email == "sdr@test.com")).first()
        campaign = db.get(Campaign, 3)
        if user is None or campaign is None:
            raise SystemExit("Falta usuario sdr@test.com o campaña id=3")

        prospect = db.scalars(
            select(Prospect).where(
                Prospect.campaign_id == campaign.id,
                Prospect.email == DEMO_EMAIL,
            )
        ).first()
        if prospect is None:
            prospect = Prospect(
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                name="Test WhatsApp D7",
                email=DEMO_EMAIL,
                company_name="Empresa Test Nexus",
                phone=DEMO_PHONE,
                whatsapp=DEMO_PHONE,
                linkedin_url="https://www.linkedin.com/in/mariaodillemarcos/",
                status=ProspectStatus.compatible.value,
                compatibility_score=85,
                interest_probability=70,
                score_reason="Prospecto demo WhatsApp D7",
                next_best_action="Ejecutar toque Día 7",
            )
            db.add(prospect)
            db.flush()
            print("prospect created", prospect.id)

        prospect.name = "Test WhatsApp D7"
        prospect.email = DEMO_EMAIL
        prospect.phone = DEMO_PHONE
        prospect.whatsapp = DEMO_PHONE
        prospect.company_name = "Empresa Test Nexus"
        prospect.linkedin_url = prospect.linkedin_url or "https://www.linkedin.com/in/mariaodillemarcos/"

        _ensure_owner(db, user=user, prospect=prospect)

        if prospect.sequence_started_at is None:
            try:
                start_prospect_sequence(db, user=user, prospect=prospect)
                print("sequence started")
            except Exception as exc:
                print(f"sequence not started ({exc}) — generá la secuencia en la UI y volvé a correr el script si hace falta")

        _reset_to_day7_ready(prospect)
        db.commit()
        db.refresh(prospect)

        nxt = next_executable_day(prospect)
        if not (prospect.sequence_playbook_draft or "").strip():
            print("sin borrador: en UI -> Generar secuencia, luego Ejecutar toque Día 7")

        db.refresh(prospect)
        print(
            "ready:",
            {
                "prospect_id": prospect.id,
                "phone": prospect.phone,
                "next_day": next_executable_day(prospect),
                "completed": sorted(_completed_days(prospect)),
                "whatsapp_dry_run_hint": "WHATSAPP_DRY_RUN=1 en backend/.env si Meta no responde",
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
