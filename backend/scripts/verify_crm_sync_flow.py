"""Smoke test CRM: touch, inbound, reunión, idempotencia y estado de sync.

Uso (desde backend/):
  python scripts/verify_crm_sync_flow.py
  python scripts/verify_crm_sync_flow.py --company-id 1
  python scripts/verify_crm_sync_flow.py --live   # llama HubSpot/SF reales si hay tokens

Por defecto usa mocks de API (valida lógica Nexus + filas crm_sync_events).
Con --live verifica también conexión OAuth por empresa.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.company import Company
from app.models.crm_sync_event import CrmSyncEvent
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.crm import company_credentials as cc
from app.services.crm import sync as crm_sync


def _line(ok: bool, msg: str) -> None:
    print(f"{'OK' if ok else 'FAIL'}: {msg}")


def _pick_company(db, company_id: int | None) -> Company | None:
    if company_id is not None:
        return db.get(Company, company_id)
    return db.scalars(select(Company).order_by(Company.id.asc())).first()


def _pick_prospect(db, company_id: int) -> Prospect | None:
    return db.scalars(
        select(Prospect)
        .where(
            Prospect.company_id == company_id,
            Prospect.email.isnot(None),
            Prospect.email != "",
        )
        .order_by(Prospect.id.desc())
    ).first()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test sync CRM Nexus")
    parser.add_argument("--company-id", type=int, default=None)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Sin mocks: usa tokens reales de HubSpot/Salesforce de la empresa",
    )
    args = parser.parse_args()

    db = SessionLocal()
    failures = 0

    try:
        company = _pick_company(db, args.company_id)
        if company is None:
            _line(False, "Empresa no encontrada")
            return 1
        company_id = int(company.id)
        _line(True, f"Empresa: {company.name} (id={company_id})")

        hs_on = cc.hubspot_active(db, company_id)
        sf_on = cc.salesforce_active(db, company_id)
        _line(hs_on or sf_on, f"CRM activo — HubSpot={hs_on} Salesforce={sf_on}")
        if not hs_on and not sf_on:
            _line(False, "CRM no conectado en esta empresa")
            if args.live:
                failures += 1
            else:
                _line(True, "Modo mock: simula HubSpot activo para validar logica")
                hs_on = True

        prospect = _pick_prospect(db, company_id)
        if prospect is None:
            _line(False, "Sin prospecto con email en la empresa")
            return 1
        _line(True, f"Prospecto: {prospect.name} <{prospect.email}> (id={prospect.id})")

        if args.live:
            from app.services.crm import hubspot, salesforce

            if hs_on:
                v = hubspot.verify_hubspot(db, company_id, deep=True)
                ok = bool(v.get("api_reachable"))
                _line(ok, f"HubSpot verify: {v.get('verification_summary')}")
                failures += 0 if ok else 1
            if sf_on:
                v = salesforce.verify_salesforce(db, company_id, deep=True)
                ok = bool(v.get("api_reachable"))
                _line(ok, f"Salesforce verify: {v.get('verification_summary')}")
                failures += 0 if ok else 1

        db.add(
            OutreachMessage(
                prospect_id=prospect.id,
                campaign_id=prospect.campaign_id,
                sender_type="sdr",
                direction="outbound",
                channel="email",
                message="Smoke test outbound previo",
            )
        )
        db.flush()

        touch_key = crm_sync.touch_event_key(day=99, channel="email")
        inbound_key = crm_sync.inbound_event_key(channel="email", message_id="smoke-inbound-1")
        meeting_key = crm_sync.meeting_event_key(meeting_id=999001)

        for old in db.scalars(
            select(CrmSyncEvent).where(
                CrmSyncEvent.prospect_id == prospect.id,
                CrmSyncEvent.event_key.in_([touch_key, inbound_key, meeting_key]),
            )
        ).all():
            db.delete(old)
        db.flush()

        patchers = []
        if not args.live:
            patchers = [
                patch("app.services.crm.sync.hubspot.upsert_contact", return_value="hs-smoke"),
                patch("app.services.crm.sync.hubspot.create_note_for_contact", return_value=True),
                patch("app.services.crm.sync.salesforce.upsert_contact", return_value="sf-smoke"),
                patch("app.services.crm.sync.salesforce.create_task_for_contact", return_value=True),
            ]
            for p in patchers:
                p.start()

        try:
            crm_sync.sync_touch_sent(
                db,
                prospect=prospect,
                day=99,
                channel="email",
                message_body="Smoke touch Nexus",
            )
            crm_sync.sync_inbound_reply(
                db,
                prospect=prospect,
                channel="email",
                message_id="smoke-inbound-1",
                message_body="Smoke respuesta prospecto",
            )
            crm_sync.sync_meeting_booked(
                db,
                prospect=prospect,
                meeting_id=999001,
                scheduled_for=datetime.now(UTC),
                title="Smoke reunión Nexus",
            )
            db.flush()

            rows = db.scalars(
                select(CrmSyncEvent).where(
                    CrmSyncEvent.prospect_id == prospect.id,
                    CrmSyncEvent.event_key.in_([touch_key, inbound_key, meeting_key]),
                )
            ).all()
            _line(len(rows) == 3, f"Eventos creados: {len(rows)}/3")

            for row in rows:
                hs_ok = (not hs_on) or row.hubspot_synced
                sf_ok = (not sf_on) or row.salesforce_synced
                _line(hs_ok, f"  {row.event_key} hubspot_synced={row.hubspot_synced}")
                _line(sf_ok, f"  {row.event_key} salesforce_synced={row.salesforce_synced}")
                if not hs_ok or not sf_ok:
                    failures += 1
                if row.hubspot_error:
                    _line(False, f"  hubspot_error: {row.hubspot_error}")
                    failures += 1
                if row.salesforce_error:
                    _line(False, f"  salesforce_error: {row.salesforce_error}")
                    failures += 1

            crm_sync.sync_touch_sent(
                db,
                prospect=prospect,
                day=99,
                channel="email",
                message_body="Duplicado",
            )
            db.flush()
            rows_touch = db.scalars(
                select(CrmSyncEvent).where(
                    CrmSyncEvent.prospect_id == prospect.id,
                    CrmSyncEvent.event_key == touch_key,
                )
            ).all()
            _line(len(rows_touch) == 1, "Idempotencia touch (un solo evento)")
            if len(rows_touch) != 1:
                failures += 1

            status = crm_sync.company_sync_status(db, company_id)
            _line(isinstance(status.get("pending_count"), int), f"pending_count={status.get('pending_count')}")
            _line(
                status.get("hubspot_active") == hs_on,
                f"status hubspot_active={status.get('hubspot_active')}",
            )

            if failures == 0:
                stats = crm_sync.retry_pending_for_company(db, company_id)
                _line(True, f"retry_pending retried={stats.get('retried')} resolved={stats.get('resolved')}")

        finally:
            for p in reversed(patchers):
                p.stop()

        db.rollback()
        _line(True, "Transacción revertida (smoke no deja basura en BD)")

    finally:
        db.close()

    print()
    if failures:
        print(f"Resultado: {failures} fallo(s)")
        return 1
    print("Resultado: CRM smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
