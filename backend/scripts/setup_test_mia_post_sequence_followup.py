"""Prepara follow-up post-secuencia vencido para Mia (campaña 4) — prueba ICP Sí/No."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.services import followup_engine

PROSPECT_ID = 10
CAMPAIGN_ID = 4


def main() -> None:
    db = SessionLocal()
    try:
        prospect = db.get(Prospect, PROSPECT_ID)
        campaign = db.scalars(
            select(Campaign)
            .where(Campaign.id == CAMPAIGN_ID)
            .options(selectinload(Campaign.product), selectinload(Campaign.company))
        ).first()
        if not prospect or not campaign:
            print("Prospecto o campaña no encontrados")
            return

        print(f"Prospecto: {prospect.name} (id={prospect.id})")
        print(f"  Estado ICP: {prospect.status}")
        print(f"  Campaña: {campaign.name} (id={campaign.id})")
        print(f"  Post-secuencia: {getattr(campaign, 'post_sequence_followup_enabled', True)}")
        print(f"  Remitente: {getattr(campaign, 'sender_name', '') or '—'}")
        company = campaign.company
        print(f"  Empresa (brand): {(company.name if company else '') or '—'}")

        if prospect.status == ProspectStatus.not_compatible.value:
            print()
            print("ICP = No compatible → el motor cancelará el follow-up automático.")
            print("Para probar Sí: en Prospectos marcá Mia como Compatible o Contactado.")
            return

        if prospect.status not in (
            ProspectStatus.contacted.value,
            ProspectStatus.compatible.value,
            ProspectStatus.interested.value,
        ):
            prospect.status = ProspectStatus.contacted.value
            print("  → Estado actualizado a contacted para habilitar follow-up.")

        prospect.sequence_completed_at = prospect.sequence_completed_at or datetime.now(UTC)
        followup_engine.cancel_pending_followup_tasks(db, prospect.id)

        due = datetime.now(UTC) - timedelta(minutes=1)
        task = OutreachTask(
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            task_kind="scheduled_followup",
            title="Follow-up post-secuencia (test Mia)",
            notes="Tarea de prueba — vencida para ejecutar ahora.",
            due_at=due,
            status="pending",
        )
        db.add(task)
        db.commit()

        print()
        print("Listo. Follow-up post-secuencia programado y vencido.")
        print("Pasos:")
        print("  1. Reiniciá backend si hace falta")
        print("  2. Campaña 4 → botón «Ejecutar follow-ups programados»")
        print("     o POST /campaigns/4/outreach/run-scheduled-followups")
        print("  3. Revisá el mensaje: firma CostGuard Demo Client + contexto ICP")
        print()
        print("ICP No: dejá status=not_compatible y volvé a correr este script (debe avisar).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
