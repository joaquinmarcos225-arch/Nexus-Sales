"""Prepara prospecto Christian Damian Mariano para demo LinkedIn (Día 1 enviado + Día 4 mensaje)."""

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
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK
from app.services.linkedin_assisted_service import CONN_CONNECTED
from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_GENERADO,
    TOUCH_PENDIENTE,
    _completed_days,
    _draft_by_day,
    _save_touch_log,
    _touch_log,
    execute_sequence_touch,
    generate_sequence_preview,
    next_executable_day,
    start_prospect_sequence,
)

DEMO_EMAIL = "fernandezjoaquinjose+christian@gmail.com"
DEMO_LINKEDIN = "https://www.linkedin.com/in/christian-damian-mariano-a745859/"
DEMO_NAME = "Christian Damian Mariano"
DEMO_LINKEDIN_MESSAGE = """Hola Christian, ¿cómo va?
Te escribí por mail desde CostGuard sobre Nexus Sales y cómo automatizamos la prospección comercial.
¿Te sirve una llamada breve esta semana para verlo?"""


def _ensure_owner(db, *, user: User, prospect: Prospect) -> None:
    if prospect.owner_user_id == user.id:
        return
    prospect.owner_user_id = user.id
    prospect.claimed_at = datetime.now(UTC)
    prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    prospect.ownership_cooldown_until = None
    db.commit()
    db.refresh(prospect)


def _reset_to_day4_message_ready(prospect: Prospect) -> None:
    """Día 1 email enviado; conexión LI = connected; Día 4 pendiente (DM, no Connect)."""
    now = datetime.now(UTC).isoformat()
    log = _touch_log(prospect)
    draft = _draft_by_day(prospect)

    day1_body = (
        (log.get("1") or {}).get("message_body")
        or (log.get("1") or {}).get("body")
        or (draft.get(1) or {}).get("body")
        or (
            "Hola Christian, te escribo desde CostGuard / Nexus Sales sobre automatizar "
            "la prospección outbound de tu equipo."
        )
    )
    log["1"] = {
        "status": TOUCH_ENVIADO,
        "sent_at": now,
        "message_body": day1_body,
        "body": day1_body,
        "subject": (draft.get(1) or {}).get("subject") or "Automatización de prospección",
    }

    for step in DEFAULT_MVP_PLAYBOOK:
        if step.day == 1:
            continue
        log[str(step.day)] = {"status": TOUCH_PENDIENTE}

    _save_touch_log(prospect, log)
    prospect.sequence_fired_milestones = json.dumps([1])
    prospect.sequence_paused = False
    prospect.sequence_completed_at = None
    prospect.sequence_group = "contactado"
    prospect.commercial_state = "prospeccion"
    prospect.linkedin_connection_status = CONN_CONNECTED
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_status = None
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    prospect.linkedin_sdr_marked_sent_at = None
    prospect.linkedin_invite_sent_at = None
    prospect.linkedin_connected_at = datetime.now(UTC)


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalars(select(User).where(User.email == "sdr@test.com")).first()
        if user is None:
            user = db.scalars(select(User).where(User.email == "director@test.com")).first()
        # Demo LinkedIn: Outbound LATAM Q1.2
        campaign = db.scalars(
            select(Campaign).where(Campaign.name == "Outbound LATAM Q1.2")
        ).first() or db.get(Campaign, 4)
        if user is None or campaign is None:
            raise SystemExit("Falta usuario sdr/director@test.com o campaña Outbound LATAM Q1.2")

        prospect = db.scalars(
            select(Prospect).where(
                Prospect.email == DEMO_EMAIL,
            )
        ).first()
        if prospect is None:
            prospect = Prospect(
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                name=DEMO_NAME,
                email=DEMO_EMAIL,
                company_name="Demo Prospecto",
                linkedin_url=DEMO_LINKEDIN,
                status=ProspectStatus.compatible.value,
                compatibility_score=88,
                interest_probability=72,
                score_reason="Prospecto demo LinkedIn Christian",
                next_best_action="Ejecutar toque Día 4 LinkedIn",
            )
            db.add(prospect)
            db.flush()
            print("prospect created", prospect.id)

        prospect.name = DEMO_NAME
        prospect.email = DEMO_EMAIL
        prospect.company_id = campaign.company_id
        prospect.campaign_id = campaign.id
        prospect.company_name = prospect.company_name or "Demo Prospecto"
        prospect.linkedin_url = DEMO_LINKEDIN
        prospect.status = ProspectStatus.compatible.value

        _ensure_owner(db, user=user, prospect=prospect)

        if not (prospect.sequence_playbook_draft or "").strip():
            print("generando secuencia (preview)...")
            try:
                generate_sequence_preview(
                    db, user=user, prospect=prospect, force_regenerate=False
                )
                db.refresh(prospect)
                print("secuencia generada")
            except Exception as exc:
                print(f"generate preview failed ({exc})")
                raise SystemExit(
                    "No se pudo generar la secuencia. Revisá campaña/producto y reintentá."
                ) from exc

        if prospect.sequence_started_at is None:
            try:
                start_prospect_sequence(db, user=user, prospect=prospect)
                print("sequence started")
            except Exception as exc:
                print(f"sequence start skipped ({exc})")

        _reset_to_day4_message_ready(prospect)
        db.commit()
        db.refresh(prospect)

        nxt = next_executable_day(prospect)
        if nxt == 4 and (prospect.sequence_playbook_draft or "").strip():
            try:
                execute_sequence_touch(db, user=user, prospect=prospect, day=4)
                db.refresh(prospect)
                print("day 4 executed -> LinkedIn message queue ready")
            except Exception as exc:
                print(f"day 4 execute skipped ({exc}) — Ejecutar Día 4 en la UI")
        elif not (prospect.sequence_playbook_draft or "").strip():
            print("sin borrador: en UI -> Generar secuencia, luego Ejecutar toque Día 4")

        # Copy determinista para la demo: nunca mostrar fallback/test ni depender de OpenAI.
        prospect.linkedin_assisted_draft = DEMO_LINKEDIN_MESSAGE
        prospect.linkedin_assist_status = "suggested"
        prospect.linkedin_assist_session_id = None
        prospect.linkedin_last_assisted_at = None
        day4_log = _touch_log(prospect)
        day4_log["4"] = {
            **(day4_log.get("4") or {}),
            "status": TOUCH_GENERADO,
            "message_body": DEMO_LINKEDIN_MESSAGE,
            "body": DEMO_LINKEDIN_MESSAGE,
            "error": None,
            "fallback_test": False,
        }
        _save_touch_log(prospect, day4_log)

        # Retirar fixtures viejas para que Christian sea la única tarea demo visible.
        stale = db.scalars(
            select(Prospect).where(
                Prospect.name.in_(["Test LinkedIn D4", DEMO_NAME]),
                Prospect.id != prospect.id,
            )
        ).all()
        for old in stale:
            if old.name == "Test LinkedIn D4":
                old.linkedin_assisted_draft = None
                old.linkedin_assist_status = "sent"
                old.linkedin_assist_session_id = None
            elif old.campaign_id != campaign.id:
                # Si quedó Christian en otra campaña, sacar de esa cola.
                old.linkedin_assisted_draft = None
                old.linkedin_assist_status = "sent"
                old.linkedin_assist_session_id = None

        db.commit()
        db.refresh(prospect)
        print(
            "ready:",
            {
                "login": f"{user.email} / demo123",
                "campaign": f"{campaign.name} (id={campaign.id})",
                "prospect_id": prospect.id,
                "prospect_name": prospect.name,
                "linkedin": prospect.linkedin_url,
                "linkedin_connection_status": prospect.linkedin_connection_status,
                "next_executable_day": next_executable_day(prospect),
                "completed_days": sorted(_completed_days(prospect)),
                "linkedin_queue": bool((prospect.linkedin_assisted_draft or "").strip()),
                "linkedin_assist_status": prospect.linkedin_assist_status,
                "ui": f"/prospectos (buscar '{DEMO_NAME}') o /campanas/{campaign.id}",
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
