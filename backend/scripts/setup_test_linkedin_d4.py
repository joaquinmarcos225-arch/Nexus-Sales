"""Prepara prospecto Test LinkedIn D4 en campaña 3 para demo (Día 4 en cola)."""

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
from app.models.enums import ProspectOwnershipStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.services import prospect_ownership as own
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK
from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_PENDIENTE,
    _completed_days,
    _draft_by_day,
    _save_touch_log,
    _touch_log,
    execute_sequence_touch,
    next_executable_day,
    start_prospect_sequence,
)

DEMO_EMAIL = "fernandezjoaquinjose+dia4@gmail.com"
DEMO_LINKEDIN = "https://www.linkedin.com/in/mariaodillemarcos/"


def _ensure_owner(db, *, user: User, prospect: Prospect) -> None:
    if prospect.owner_user_id == user.id:
        return
    prospect.owner_user_id = user.id
    prospect.claimed_at = datetime.now(UTC)
    prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    prospect.ownership_cooldown_until = None
    db.commit()
    db.refresh(prospect)


def _reset_to_day4_ready(prospect: Prospect) -> None:
    """Día 1 enviado; Día 4 pendiente (próximo ejecutable)."""
    now = datetime.now(UTC).isoformat()
    log = _touch_log(prospect)
    draft = _draft_by_day(prospect)

    day1 = dict(log.get("1", {}))
    day1_body = (
        day1.get("message_body")
        or day1.get("body")
        or (draft.get(1) or {}).get("body")
        or "Hola, te escribo desde CostGuard Demo Client sobre nuestra plataforma de ventas."
    )
    log["1"] = {
        "status": TOUCH_ENVIADO,
        "sent_at": day1.get("sent_at") or now,
        "message_body": day1_body,
        "body": day1_body,
    }

    for step in DEFAULT_MVP_PLAYBOOK:
        if step.day == 1:
            continue
        log[str(step.day)] = {"status": TOUCH_PENDIENTE}

    _save_touch_log(prospect, log)
    prospect.sequence_fired_milestones = json.dumps([1])
    prospect.sequence_paused = False
    prospect.sequence_group = "contactado"
    prospect.commercial_state = "prospeccion"
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_status = None
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    prospect.linkedin_sdr_marked_sent_at = None


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
            prospect = db.scalars(
                select(Prospect).where(
                    Prospect.campaign_id == campaign.id,
                    Prospect.id == 2,
                )
            ).first()

        if prospect is None:
            raise SystemExit("No hay prospecto base para reutilizar (id=2 o email +dia4).")

        prospect.name = "Test LinkedIn D4"
        prospect.email = DEMO_EMAIL
        prospect.company_name = "Empresa Test Nexus"
        prospect.linkedin_url = DEMO_LINKEDIN
        prospect.phone = prospect.phone or None
        prospect.whatsapp = prospect.whatsapp or None

        _ensure_owner(db, user=user, prospect=prospect)

        if prospect.sequence_started_at is None:
            start_prospect_sequence(db, user=user, prospect=prospect)
            print("sequence started")
        else:
            print("sequence already started")

        _reset_to_day4_ready(prospect)
        db.commit()
        db.refresh(prospect)

        nxt = next_executable_day(prospect)
        if nxt == 4 and (prospect.sequence_playbook_draft or "").strip():
            try:
                execute_sequence_touch(db, user=user, prospect=prospect, day=4)
                db.refresh(prospect)
                print("day 4 executed -> LinkedIn queue ready")
            except Exception as exc:
                print(f"day 4 execute skipped ({exc}) — ejecutalo manual en la UI")
        elif not (prospect.sequence_playbook_draft or "").strip():
            print("sin borrador de secuencia: en UI -> Generar secuencia, luego Ejecutar toque Día 4")

        db.refresh(prospect)
        print(
            "ready:",
            {
                "login": "sdr@test.com / demo123",
                "campaign": f"{campaign.name} (id={campaign.id})",
                "prospect_id": prospect.id,
                "prospect_name": prospect.name,
                "linkedin": prospect.linkedin_url,
                "next_executable_day": next_executable_day(prospect),
                "completed_days": sorted(_completed_days(prospect)),
                "linkedin_queue": bool((prospect.linkedin_assisted_draft or "").strip()),
                "linkedin_assist_status": prospect.linkedin_assist_status,
                "sequence_paused": prospect.sequence_paused,
                "ui": f"/prospectos (buscar '{prospect.name}') o /campanas/{campaign.id}",
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
