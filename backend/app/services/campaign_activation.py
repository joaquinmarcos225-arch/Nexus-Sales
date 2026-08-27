"""Activar / pausar campaña: sourcing en background + primer outreach."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.connected_account import ConnectedAccount
from app.models.enums import CampaignStatus, IntegrationProvider, IntegrationStatus
from app.models.outreach import OutreachSequence
from app.services import multichannel_sequence as mseq

_logger = logging.getLogger(__name__)


def _schedule_individual_kickoffs_background(
    *,
    campaign_id: int,
    prospect_ids: list[int],
    actor_user_id: int,
) -> None:
    """Kickoff de secuencias individuales fuera del request HTTP."""
    import threading

    ids = [int(x) for x in prospect_ids if x]

    def _worker() -> None:
        from app.database.session import SessionLocal
        from app.models.user import User
        from app.services.manual_sequence_kickoff import kickoff_individual_sequence_for_prospect

        db = SessionLocal()
        try:
            campaign = db.get(Campaign, campaign_id)
            actor = db.get(User, actor_user_id)
            if campaign is None or actor is None:
                return
            from app.models.prospect import Prospect

            for pid in ids:
                prospect = db.get(Prospect, pid)
                if prospect is None or prospect.sequence_started_at is not None:
                    continue
                try:
                    kickoff_individual_sequence_for_prospect(
                        db,
                        actor=actor,
                        campaign=campaign,
                        prospect=prospect,
                        wait_for_enrich=False,
                    )
                    db.commit()
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    _logger.warning(
                        "[campaign-activation] bg kickoff failed campaign=%s prospect=%s: %s",
                        campaign_id,
                        pid,
                        str(getattr(exc, "detail", None) or exc)[:300],
                    )
        finally:
            db.close()

    threading.Thread(
        target=_worker,
        name=f"individual-kickoff-{campaign_id}",
        daemon=True,
    ).start()


def seller_has_gmail(db: Session, company_id: int, user_id: int) -> bool:
    row = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.gmail.value,
            ConnectedAccount.status == IntegrationStatus.connected.value,
        )
    ).first()
    return row is not None


def _get_or_create_sequence(db: Session, campaign_id: int) -> OutreachSequence:
    seq = db.scalars(
        select(OutreachSequence).where(OutreachSequence.campaign_id == campaign_id)
    ).first()
    if seq is None:
        seq = OutreachSequence(campaign_id=campaign_id, is_running=False, current_step=0)
        db.add(seq)
        db.flush()
    return seq


def activate_campaign(db: Session, campaign_id: int) -> dict[str, Any]:
    """
    Marca campaña activa, enciende secuencia y dispara outreach inicial (Gmail si está conectado).

    La búsqueda/importación de prospectos ICP corre en background para que el start
    responda al toque (la UI no se queda en "Buscando prospectos…").
    """
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        return {"ok": False, "detail": "Campaña no encontrada"}

    if not campaign.seller_id:
        return {
            "ok": False,
            "detail": "Asigná un vendedor a la campaña antes de iniciarla.",
            "gmail_connected": False,
        }

    gmail_ok = seller_has_gmail(db, campaign.company_id, int(campaign.seller_id))

    sourcing: dict[str, Any] = {}
    defer_sourcing = False
    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if is_individual_container_campaign(campaign):
        # Ya tiene el prospecto cargado a mano: solo secuencia de envíos, sin buscar más.
        count = 0
        try:
            from app.services.campaign_prospects import count_campaign_prospects

            count = count_campaign_prospects(db, campaign.id)
        except Exception:  # noqa: BLE001
            count = 0
        sourcing = {
            "ran": False,
            "skipped": True,
            "ok": True,
            "reason": "individual_container",
            "imported": 0,
            "prospect_count_before": count,
            "prospect_count_after": count,
            "prospect_count_target": int(campaign.prospect_count or 0),
            "message": "Secuencia individual: sin búsqueda de prospectos.",
        }
    else:
        from app.services.campaign_prospects import count_campaign_prospects

        count = count_campaign_prospects(db, campaign.id)
        target = max(0, int(campaign.prospect_count or 0))
        defer_sourcing = target > 0 and count < target
        if defer_sourcing:
            sourcing = {
                "ran": False,
                "queued": True,
                "ok": True,
                "imported": 0,
                "prospect_count_before": count,
                "prospect_count_after": count,
                "prospect_count_target": target,
                "message": (
                    "Campaña iniciada. Nexus busca e importa prospectos en segundo plano."
                ),
            }
            mseq._append_log(
                campaign,
                "Campaña en marcha: buscando prospectos ICP en segundo plano…",
                kind="sourcing",
            )
        else:
            sourcing = {
                "ran": False,
                "skipped": True,
                "ok": True,
                "reason": "quota_met" if count >= target else "no_prospect_quota",
                "imported": 0,
                "prospect_count_before": count,
                "prospect_count_after": count,
                "prospect_count_target": target,
                "quota_met": count >= target > 0,
                "message": (
                    f"La campaña ya tiene {count} de {target} prospecciones."
                    if count >= target > 0
                    else None
                ),
            }

    campaign.status = CampaignStatus.running.value
    campaign.automation_paused = False
    if getattr(campaign, "updated_at", None) is not None:
        campaign.updated_at = datetime.now(UTC)

    seq = _get_or_create_sequence(db, campaign_id)
    seq.is_running = True
    seq.current_step = int(seq.current_step or 0) + 1

    individual_kickoffs: dict[str, Any] = {"started": 0, "errors": []}
    if is_individual_container_campaign(campaign):
        from app.models.enums import ProspectStatus
        from app.models.prospect import Prospect
        from app.models.user import User
        from app.services.manual_sequence_kickoff import kickoff_individual_sequence_for_prospect

        actor = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
        # Manuales pueden quedar not_compatible por ICP vacío: igual hay que arrancarlos.
        pending = db.scalars(
            select(Prospect).where(
                Prospect.campaign_id == campaign.id,
                Prospect.sequence_started_at.is_(None),
                Prospect.status.notin_(
                    [
                        ProspectStatus.not_interested.value,
                        ProspectStatus.meeting_booked.value,
                        ProspectStatus.failed.value,
                    ]
                ),
            )
        ).all()

        # Antes de kickoff: arrancar búsqueda de canales faltantes si hace falta.
        enrich_id_set: set[int] = set()
        try:
            from app.services.manual_channel_enrich_job import (
                STATUS_DONE,
                STATUS_SEARCHING,
                STATUS_SKIPPED,
                STATUS_TIMED_OUT,
                begin_manual_channel_enrich,
                schedule_manual_channel_enrich,
            )

            plan = getattr(campaign, "sequence_plan", None)
            enrich_ids: list[int] = []
            for prospect in pending:
                st = (getattr(prospect, "channel_enrich_status", None) or "").strip().lower()
                if st in (STATUS_DONE, STATUS_TIMED_OUT, STATUS_SKIPPED, STATUS_SEARCHING):
                    if st == STATUS_SEARCHING:
                        enrich_ids.append(int(prospect.id))
                    continue
                meta = begin_manual_channel_enrich(
                    db, prospect, sequence_plan=plan if isinstance(plan, dict) else None
                )
                if meta.get("enriching"):
                    enrich_ids.append(int(prospect.id))
            enrich_id_set = set(enrich_ids)
            # Commit + background: NO bloquear el HTTP esperando Prospeo (evita 504).
            if enrich_ids:
                db.commit()
                for pid in enrich_ids:
                    schedule_manual_channel_enrich(
                        pid,
                        actor_user_id=int(actor.id) if actor else None,
                        kickoff_if_running=True,
                    )
                db.refresh(campaign)
                if actor is not None:
                    db.refresh(actor)
                for prospect in pending:
                    try:
                        db.refresh(prospect)
                    except Exception:  # noqa: BLE001
                        pass
                mseq._append_log(
                    campaign,
                    f"Buscando datos faltantes de {len(enrich_ids)} prospecto(s)… "
                    "La secuencia arranca al completar la búsqueda.",
                    kind="sequence",
                )
        except Exception as exc:  # noqa: BLE001
            enrich_id_set = set()
            _logger.info(
                "[campaign-activation] pre-kickoff enrich begin skipped campaign=%s: %s",
                campaign_id,
                exc,
            )

        # Kickoff en background (nunca bloquear HTTP con Gmail/OpenAI).
        ready_ids: list[int] = []
        for prospect in pending:
            if int(prospect.id) in enrich_id_set:
                continue
            st = (getattr(prospect, "channel_enrich_status", None) or "").strip().lower()
            if st == "searching":
                continue
            if actor is None:
                individual_kickoffs["errors"].append(
                    {"prospect_id": prospect.id, "detail": "Sin vendedor asignado."}
                )
                continue
            ready_ids.append(int(prospect.id))

        if ready_ids and actor is not None:
            db.commit()
            _schedule_individual_kickoffs_background(
                campaign_id=int(campaign.id),
                prospect_ids=ready_ids,
                actor_user_id=int(actor.id),
            )
            individual_kickoffs["started"] = 0
            individual_kickoffs["queued"] = len(ready_ids)
            mseq._append_log(
                campaign,
                f"Arrancando secuencia de {len(ready_ids)} prospecto(s) en segundo plano…",
                kind="sequence",
            )

        if enrich_id_set:
            individual_kickoffs["deferred_enrich"] = len(enrich_id_set)
        bootstrap = {
            "day1_sent": 0,
            "used_gmail": True,
            "individual_kickoffs": individual_kickoffs,
            "error_messages": [e["detail"] for e in individual_kickoffs["errors"]],
            "errors": len(individual_kickoffs["errors"]),
            "channel_enrich_pending": len(enrich_id_set),
            "kickoff_queued": len(ready_ids),
        }
        if enrich_id_set:
            mseq._append_log(
                campaign,
                "Secuencia en marcha: buscando información de canales antes del primer toque…",
                kind="sequence",
            )
    else:
        # Arranque inmediato: no bloquear el HTTP con Gmail/OpenAI/bootstrap.
        # El outreach inicial corre en background junto con el sourcing.
        enrich_pending = 0
        try:
            from app.models.enums import ProspectStatus
            from app.models.prospect import Prospect
            from app.services.manual_channel_enrich_job import (
                STATUS_DONE,
                STATUS_SEARCHING,
                STATUS_SKIPPED,
                STATUS_TIMED_OUT,
                begin_manual_channel_enrich,
                schedule_manual_channel_enrich,
            )

            pending_classic = db.scalars(
                select(Prospect).where(
                    Prospect.campaign_id == campaign.id,
                    Prospect.status.notin_(
                        [
                            ProspectStatus.not_interested.value,
                            ProspectStatus.meeting_booked.value,
                            ProspectStatus.failed.value,
                        ]
                    ),
                )
            ).all()
            plan = getattr(campaign, "sequence_plan", None)
            enrich_ids: list[int] = []
            for prospect in pending_classic:
                st = (getattr(prospect, "channel_enrich_status", None) or "").strip().lower()
                if st in (STATUS_DONE, STATUS_TIMED_OUT, STATUS_SKIPPED, STATUS_SEARCHING):
                    if st == STATUS_SEARCHING:
                        enrich_ids.append(int(prospect.id))
                    continue
                meta = begin_manual_channel_enrich(
                    db, prospect, sequence_plan=plan if isinstance(plan, dict) else None
                )
                if meta.get("enriching"):
                    enrich_ids.append(int(prospect.id))
            if enrich_ids:
                # Commit para que el worker vea searching (misma razón que individuales).
                db.commit()
                for pid in enrich_ids:
                    schedule_manual_channel_enrich(
                        pid,
                        actor_user_id=int(campaign.seller_id) if campaign.seller_id else None,
                        kickoff_if_running=False,
                    )
                enrich_pending = len(enrich_ids)
                try:
                    db.refresh(campaign)
                except Exception:  # noqa: BLE001
                    pass
                mseq._append_log(
                    campaign,
                    f"Buscando información de canales de {enrich_pending} prospecto(s)…",
                    kind="sequence",
                )
        except Exception as exc:  # noqa: BLE001
            _logger.info(
                "[campaign-activation] classic enrich begin skipped campaign=%s: %s",
                campaign_id,
                exc,
            )

        bootstrap = {
            "day1_sent": 0,
            "drafts": 0,
            "sent": 0,
            "skipped": 0,
            "errors": 0,
            "error_messages": [],
            "used_gmail": gmail_ok,
            "deferred": True,
            "channel_enrich_pending": enrich_pending,
        }
        # Siempre encolar background: si falta cupo busca; si está lleno prepara día 1.
        # NO reescribir sourcing_queued=True cuando el cupo ya está completo (UI falsa
        # de "buscando prospectos…" y el usuario espera una búsqueda que no corre).
        defer_sourcing = True
        if sourcing.get("queued"):
            pass
        elif sourcing.get("quota_met") or sourcing.get("reason") == "quota_met":
            from app.services.campaign_prospects import count_campaign_prospects

            count = count_campaign_prospects(db, campaign.id)
            target = max(0, int(campaign.prospect_count or 0))
            sourcing = {
                "ran": False,
                "queued": False,
                "ok": True,
                "reason": "quota_met",
                "imported": 0,
                "prospect_count_before": count,
                "prospect_count_after": count,
                "prospect_count_target": target,
                "quota_met": True,
                "message": (
                    f"Cupo completo ({count}/{target}). "
                    "Preparando toques día 1 en segundo plano (LinkedIn verifica contacto)."
                ),
            }
            mseq._append_log(
                campaign,
                "Campaña en marcha: cupo completo — preparando outreach día 1…",
                kind="sequence",
            )
        else:
            from app.services.campaign_prospects import count_campaign_prospects

            count = count_campaign_prospects(db, campaign.id)
            target = max(0, int(campaign.prospect_count or 0))
            need_search = target > 0 and count < target
            sourcing = {
                "ran": False,
                "queued": need_search,
                "ok": True,
                "imported": 0,
                "prospect_count_before": count,
                "prospect_count_after": count,
                "prospect_count_target": target,
                "quota_met": (not need_search) and target > 0,
                "message": (
                    "Campaña iniciada. Nexus busca e importa prospectos en segundo plano."
                    if need_search
                    else "Campaña iniciada. Preparando outreach en segundo plano."
                ),
            }
            mseq._append_log(
                campaign,
                (
                    "Campaña en marcha: buscando prospectos y preparando outreach…"
                    if need_search
                    else "Campaña en marcha: preparando outreach en segundo plano…"
                ),
                kind="sourcing" if need_search else "sequence",
            )
    contacted = int(bootstrap.get("day1_sent") or 0) + int(bootstrap.get("drafts") or 0) + int(
        bootstrap.get("sent") or 0
    )
    if contacted == 0:
        contacted = int(bootstrap.get("contacted_now") or 0)

    mseq._append_log(
        campaign,
        "Campaña iniciada: Nexus procesará outreach, respuestas y follow-ups en automático.",
        kind="sequence",
    )

    return {
        "ok": True,
        "sequence": seq,
        "campaign": campaign,
        "gmail_connected": gmail_ok,
        "contacted_now": contacted,
        "drafts": int(bootstrap.get("drafts") or 0),
        "sent": int(bootstrap.get("sent") or 0),
        "skipped": int(bootstrap.get("skipped") or 0),
        "errors": int(bootstrap.get("errors") or 0),
        "error_messages": list(bootstrap.get("error_messages") or []),
        "used_gmail": bool(bootstrap.get("used_gmail")),
        "simulated": bool(bootstrap.get("simulated")),
        "sourcing_ran": bool(sourcing.get("ran")),
        "sourcing_queued": bool(sourcing.get("queued") or defer_sourcing),
        "defer_sourcing": defer_sourcing,
        "sourcing_imported": int(sourcing.get("imported") or 0),
        "sourcing_quota_met": bool(sourcing.get("quota_met")),
        "sourcing_prospect_count_after": int(sourcing.get("prospect_count_after") or 0),
        "sourcing_prospect_count_target": int(sourcing.get("prospect_count_target") or 0),
        "sourcing_message": sourcing.get("message"),
        "channel_enrich_pending": int(bootstrap.get("channel_enrich_pending") or 0),
    }


def pause_campaign(db: Session, campaign_id: int) -> OutreachSequence | None:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None

    campaign.automation_paused = True
    if campaign.status == CampaignStatus.running.value:
        campaign.status = CampaignStatus.paused.value
    if getattr(campaign, "updated_at", None) is not None:
        campaign.updated_at = datetime.now(UTC)

    seq = _get_or_create_sequence(db, campaign_id)
    seq.is_running = False

    mseq._append_log(campaign, "Campaña pausada: automatización detenida.", kind="info")
    return seq
