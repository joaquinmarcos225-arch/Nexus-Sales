"""Ticks de automatización en NEXUS_REAL_MODE (Gmail inbound, Calendar, follow-ups, primer contacto)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.models.outreach import OutreachSequence
from app.services import followup_engine
from app.services import multichannel_sequence as mseq
from app.services import outreach_metrics as om
from app.services.ai_instruction_context import campaign_education_blob
from app.services.automation_job_lock import finish_job_error, finish_job_success, try_acquire_job
from app.services.gmail_automation_flags import gmail_automation_enabled, log_gmail_automation_skipped
from app.services.gmail_inbound_sync import sync_campaign_gmail_inbound
from app.services.google_calendar_sync import sync_calendar_events_for_seller
from app.services.inbound_auto_reply import process_due_inbound_auto_reply_tasks
from app.services.manual_sequence_kickoff import try_find_gmail_operator
from app.services.real_initial_outreach import ensure_outreach_sequence_running, process_campaign_initial_outreach
from app.models.user import User

JOB_PLAN_RENEWAL = "automation:tick_plan_renewal"
_DEFAULT_LOCK_PLAN_RENEWAL = 300

JOB_CRM_EXCLUSIONS = "automation:tick_crm_exclusions"
JOB_CRM_OUTBOUND = "automation:tick_crm_outbound"
_DEFAULT_LOCK_CRM_EXCLUSIONS = 900
_DEFAULT_LOCK_CRM_OUTBOUND = 180

_DEFAULT_LOCK_INBOUND_REPLY = 60
JOB_INBOUND_AUTO_REPLY = "automation:tick_inbound_auto_reply"

JOB_GMAIL_INBOUND = "automation:tick_gmail_inbound"
JOB_CALENDAR = "automation:tick_calendar_sync"
JOB_FOLLOWUPS = "automation:tick_followups"
JOB_INITIAL_OUTREACH = "automation:tick_initial_outreach"
JOB_SEQUENCE_TOUCHES = "automation:tick_sequence_touches"
JOB_SOURCING_REFILL = "automation:tick_sourcing_refill"

_DEFAULT_LOCK_GMAIL = 90
_DEFAULT_LOCK_CAL = 180
_DEFAULT_LOCK_FU = 120
_DEFAULT_LOCK_INITIAL = 120
_DEFAULT_LOCK_SEQUENCE_TOUCHES = 180
_DEFAULT_LOCK_SOURCING_REFILL = 600


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _automation_engine_active() -> bool:
    """Motor en background: modo real o scheduler habilitado."""
    return om.is_real_mode() or _truthy_env("NEXUS_AUTOMATION_SCHEDULER")


def _seller_has_gmail(db: Session, company_id: int, user_id: int) -> bool:
    row = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.gmail.value,
            ConnectedAccount.status == IntegrationStatus.connected.value,
        )
    ).first()
    return row is not None


def _company_automation_stopped(db: Session, company_id: int) -> bool:
    company = db.get(Company, company_id)
    return bool(company and getattr(company, "global_automation_stop", False))


def _active_campaigns(db: Session) -> list[Campaign]:
    by_status = list(
        db.scalars(
            select(Campaign)
            .where(
                Campaign.status.in_(("ready", "running")),
                Campaign.automation_paused.is_(False),
            )
            .options(selectinload(Campaign.product))
        ).all()
    )
    by_sequence = list(
        db.scalars(
            select(Campaign)
            .join(OutreachSequence, OutreachSequence.campaign_id == Campaign.id)
            .where(
                OutreachSequence.is_running.is_(True),
                Campaign.automation_paused.is_(False),
            )
            .options(selectinload(Campaign.product))
        ).all()
    )
    seen: set[int] = set()
    out: list[Campaign] = []
    for c in by_status + by_sequence:
        if c.id in seen:
            continue
        if _company_automation_stopped(db, c.company_id):
            continue
        seen.add(c.id)
        out.append(c)
    return out


def run_gmail_inbound_tick() -> dict[str, Any]:
    if not gmail_automation_enabled():
        log_gmail_automation_skipped("run_gmail_inbound_tick")
        return {"skipped": True, "reason": "gmail_automation_disabled"}
    if not _automation_engine_active():
        logger.info("gmail inbound tick skipped: automation_disabled")
        return {"skipped": True, "reason": "automation_disabled"}

    logger.info("gmail inbound tick started")
    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_GMAIL_INBOUND, lock_ttl_seconds=_DEFAULT_LOCK_GMAIL) is None:
            logger.info("gmail inbound tick skipped: locked")
            return {"skipped": True, "reason": "locked"}
        campaigns = _active_campaigns(db)
        campaign_ids = [c.id for c in campaigns]
        logger.info(
            "gmail inbound tick campaigns=%s ids=%s",
            len(campaigns),
            campaign_ids[:32],
        )
        imported_total = 0
        threads_total = 0
        replies_total = 0
        matched_total = 0
        errors: list[str] = []
        per_campaign: list[dict[str, Any]] = []
        for c in campaigns:
            # Misma regla que outbound: preferí Gmail del seller; si no, otro de la empresa.
            preferred = db.get(User, int(c.seller_id)) if c.seller_id else None
            operator = try_find_gmail_operator(
                db, company_id=int(c.company_id), preferred=preferred
            )
            if operator is None:
                logger.info(
                    "gmail inbound skip campaign=%s: no gmail in company (seller=%s)",
                    c.id,
                    c.seller_id,
                )
                continue
            if preferred is not None and int(operator.id) != int(preferred.id):
                logger.info(
                    "gmail inbound campaign=%s using company gmail operator=%s "
                    "(seller=%s sin Gmail)",
                    c.id,
                    operator.id,
                    c.seller_id,
                )
            try:
                stats = sync_campaign_gmail_inbound(
                    db,
                    company_id=c.company_id,
                    user_id=operator.id,
                    campaign_id=c.id,
                    allow_company_gmail_operator=True,
                )
                db.commit()
                imp = int(stats.get("imported") or 0)
                imported_total += imp
                threads_total += int(stats.get("threads_examined") or 0)
                replies_total += int(stats.get("replies_detected") or 0)
                matched_total += int(stats.get("prospects_matched") or 0)
                per_campaign.append(
                    {
                        "campaign_id": c.id,
                        "imported": imp,
                        "replies_detected": stats.get("replies_detected"),
                        "threads_examined": stats.get("threads_examined"),
                        "prospects_matched": stats.get("prospects_matched"),
                        "auto_drafts": stats.get("auto_drafts"),
                        "auto_sent": stats.get("auto_sent"),
                        "trace_tail": (stats.get("trace") or [])[-6:],
                    }
                )
                if imp > 0:
                    logger.info(
                        "gmail inbound campaign=%s imported=%s replies=%s",
                        c.id,
                        imp,
                        stats.get("replies_detected"),
                    )
                for e in stats.get("errors") or []:  # type: ignore[union-attr]
                    if isinstance(e, str):
                        errors.append(f"campaign={c.id}: {e[:400]}")
            except Exception as exc:
                db.rollback()
                errors.append(f"campaign={c.id}: {type(exc).__name__}: {exc}"[:500])
                logger.exception("gmail inbound tick failed campaign_id=%s", c.id)
        meta = {
            "campaigns_considered": len(campaigns),
            "campaign_ids": campaign_ids[:48],
            "imported": imported_total,
            "threads_examined": threads_total,
            "replies_detected": replies_total,
            "prospects_matched": matched_total,
            "per_campaign": per_campaign[:24],
            "errors": errors[:24],
            "finished_at": datetime.now(UTC).isoformat(),
        }
        logger.info(
            "gmail inbound tick finished imported=%s replies_detected=%s campaigns=%s",
            imported_total,
            replies_total,
            len(campaigns),
        )
        finish_job_success(db, JOB_GMAIL_INBOUND, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_GMAIL_INBOUND, e)
        raise
    finally:
        db.close()


def run_calendar_sync_tick() -> dict[str, Any]:
    logger.info("[calendar_sync] automation:tick_calendar_sync started")
    if not _automation_engine_active():
        logger.info("[calendar_sync] tick skipped: automation_disabled")
        return {"skipped": True, "reason": "automation_disabled"}

    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_CALENDAR, lock_ttl_seconds=_DEFAULT_LOCK_CAL) is None:
            logger.info("[calendar_sync] tick skipped: job locked")
            return {"skipped": True, "reason": "locked"}
        campaigns = _active_campaigns(db)
        pairs: set[tuple[int, int]] = set()
        for c in campaigns:
            if not _seller_has_gmail(db, c.company_id, c.seller_id):
                continue
            pairs.add((c.company_id, c.seller_id))
        errors: list[str] = []
        matched = updated = pipeline_updated = reconciled = 0
        per_seller: list[dict[str, Any]] = []
        for company_id, user_id in pairs:
            try:
                stats = sync_calendar_events_for_seller(
                    db,
                    company_id=company_id,
                    user_id=user_id,
                    campaign_id=None,
                    include_debug=True,
                    debug_max_events=12,
                    client_now_utc=datetime.now(UTC),
                )
                db.commit()
                m = int(stats.get("matched") or 0)
                c = int(stats.get("created") or 0)
                u = int(stats.get("updated") or 0)
                p = int(stats.get("pipeline_updated") or 0)
                r = int(stats.get("reconciled_operational_groups") or 0)
                matched += m
                updated += c + u
                pipeline_updated += p
                reconciled += r
                per_seller.append(
                    {
                        "company_id": company_id,
                        "user_id": user_id,
                        "matched": m,
                        "created": c,
                        "updated": u,
                        "pipeline_updated": p,
                        "reconciled": r,
                        "events_seen": stats.get("events_seen"),
                        "prospects_indexed": stats.get("prospects_with_email"),
                        "email_collisions": len(stats.get("email_collisions") or []),
                        "debug_tail": (stats.get("debug") or [])[-4:],
                    }
                )
                logger.info(
                    "[calendar_sync] seller_sync company=%s user=%s matched=%s pipeline=%s reconciled=%s",
                    company_id,
                    user_id,
                    m,
                    p,
                    r,
                )
            except Exception as exc:
                db.rollback()
                errors.append(f"{company_id}/{user_id}: {type(exc).__name__}: {exc}"[:500])
                logger.exception(
                    "[calendar_sync] seller_sync failed company=%s user=%s",
                    company_id,
                    user_id,
                )
        meta = {
            "sellers_synced": len(pairs),
            "matched": matched,
            "meetings_touched": updated,
            "pipeline_updated": pipeline_updated,
            "reconciled_operational_groups": reconciled,
            "per_seller": per_seller[:16],
            "errors": errors[:24],
            "finished_at": datetime.now(UTC).isoformat(),
        }
        logger.info(
            "[calendar_sync] automation:tick_calendar_sync finished matched=%s pipeline=%s reconciled=%s",
            matched,
            pipeline_updated,
            reconciled,
        )
        finish_job_success(db, JOB_CALENDAR, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_CALENDAR, e)
        raise
    finally:
        db.close()


def run_followups_tick() -> dict[str, Any]:
    if not _automation_engine_active():
        return {"skipped": True, "reason": "automation_disabled"}

    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_FOLLOWUPS, lock_ttl_seconds=_DEFAULT_LOCK_FU) is None:
            return {"skipped": True, "reason": "locked"}
        campaigns = _active_campaigns(db)
        total_def = total_proc = total_skip = total_err = 0
        errors: list[str] = []
        for c in campaigns:
            try:
                blob = campaign_education_blob(db, c)
                dr = mseq.process_due_deferred_resume_tasks(db, c)
                total_def += int(dr or 0)
                st = followup_engine.run_due_followups_for_campaign(db, c.id, education=blob)
                db.commit()
                total_proc += int(st.get("processed") or 0)
                total_skip += int(st.get("skipped") or 0)
                total_err += int(st.get("errors") or 0)
            except Exception as exc:
                db.rollback()
                errors.append(f"campaign={c.id}: {type(exc).__name__}: {exc}"[:500])
        meta = {
            "campaigns": len(campaigns),
            "deferred_resumed": total_def,
            "followups_processed": total_proc,
            "followups_skipped": total_skip,
            "followups_errors": total_err,
            "errors": errors[:24],
            "finished_at": datetime.now(UTC).isoformat(),
        }
        finish_job_success(db, JOB_FOLLOWUPS, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_FOLLOWUPS, e)
        raise
    finally:
        db.close()


def run_inbound_auto_reply_tick() -> dict[str, Any]:
    logger.info("[inbound_auto_reply] automation:tick_inbound_auto_reply started")
    if not gmail_automation_enabled():
        log_gmail_automation_skipped("run_inbound_auto_reply_tick")
        return {"skipped": True, "reason": "gmail_automation_disabled"}
    if not _automation_engine_active():
        logger.info(
            "[inbound_auto_reply] tick skipped: automation_disabled (real_mode=%s scheduler=%s)",
            om.is_real_mode(),
            _truthy_env("NEXUS_AUTOMATION_SCHEDULER"),
        )
        return {"skipped": True, "reason": "automation_disabled"}

    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_INBOUND_AUTO_REPLY, lock_ttl_seconds=_DEFAULT_LOCK_INBOUND_REPLY) is None:
            logger.info("[inbound_auto_reply] tick skipped: job locked")
            return {"skipped": True, "reason": "locked"}
        meta = process_due_inbound_auto_reply_tasks(db)
        db.commit()
        meta["finished_at"] = datetime.now(UTC).isoformat()
        logger.info(
            "[inbound_auto_reply] automation:tick_inbound_auto_reply finished sent=%s drafted=%s "
            "due=%s pending_all=%s errors=%s",
            meta.get("sent"),
            meta.get("drafted"),
            meta.get("due_tasks"),
            meta.get("pending_all"),
            meta.get("errors"),
        )
        finish_job_success(db, JOB_INBOUND_AUTO_REPLY, meta=meta)
        return meta
    except BaseException as e:
        logger.exception("[inbound_auto_reply] automation:tick_inbound_auto_reply failed")
        finish_job_error(db, JOB_INBOUND_AUTO_REPLY, e)
        raise
    finally:
        db.close()


def _campaigns_for_initial_outreach(db: Session) -> list[Campaign]:
    """Campañas con secuencia en marcha (o running que activamos en el tick)."""
    rows = list(
        db.scalars(
            select(Campaign)
            .join(OutreachSequence, OutreachSequence.campaign_id == Campaign.id)
            .where(
                Campaign.status.in_(("ready", "running")),
                Campaign.automation_paused.is_(False),
                OutreachSequence.is_running.is_(True),
            )
            .options(selectinload(Campaign.product))
        ).all()
    )
    extra: list[Campaign] = list(
        db.scalars(
            select(Campaign)
            .where(
                Campaign.status == "running",
                Campaign.automation_paused.is_(False),
            )
            .options(selectinload(Campaign.product))
        ).all()
    )
    seen = {c.id for c in rows}
    for c in extra:
        if c.id not in seen:
            seq = ensure_outreach_sequence_running(db, c, force=True)
            if seq.is_running:
                rows.append(c)
                seen.add(c.id)
    return rows


def run_initial_outreach_tick() -> dict[str, Any]:
    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_INITIAL_OUTREACH, lock_ttl_seconds=_DEFAULT_LOCK_INITIAL) is None:
            return {"skipped": True, "reason": "locked"}
        campaigns = _campaigns_for_initial_outreach(db)
        total_drafts = total_sent = total_skip = total_err = 0
        errors: list[str] = []
        batch = int(os.getenv("NEXUS_INITIAL_OUTREACH_BATCH_SIZE", "500"))
        batch = max(1, min(batch, 500))
        for c in campaigns:
            if not _seller_has_gmail(db, c.company_id, c.seller_id):
                continue
            try:
                blob = campaign_education_blob(db, c)
                st = process_campaign_initial_outreach(db, c, blob, max_batch=batch)
                db.commit()
                total_drafts += int(st.get("drafts") or 0)
                total_sent += int(st.get("sent") or 0)
                total_skip += int(st.get("skipped") or 0)
                total_err += int(st.get("errors") or 0)
            except Exception as exc:
                db.rollback()
                errors.append(f"campaign={c.id}: {type(exc).__name__}: {exc}"[:500])
        meta = {
            "campaigns": len(campaigns),
            "drafts": total_drafts,
            "sent": total_sent,
            "skipped": total_skip,
            "errors": total_err,
            "errors_detail": errors[:24],
            "finished_at": datetime.now(UTC).isoformat(),
        }
        finish_job_success(db, JOB_INITIAL_OUTREACH, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_INITIAL_OUTREACH, e)
        raise
    finally:
        db.close()


def run_sequence_touches_tick() -> dict[str, Any]:
    from app.services.sequence_touch_scheduler import (
        process_active_campaigns_scheduled_touches,
        sequence_touches_scheduler_enabled,
    )

    if not sequence_touches_scheduler_enabled():
        return {"skipped": True, "reason": "sequence_touches_disabled"}

    if not _automation_engine_active():
        return {"skipped": True, "reason": "automation_disabled"}

    logger.info("sequence touches tick started")
    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_SEQUENCE_TOUCHES, lock_ttl_seconds=_DEFAULT_LOCK_SEQUENCE_TOUCHES) is None:
            return {"skipped": True, "reason": "locked"}
        campaigns = _campaigns_for_initial_outreach(db)
        meta = process_active_campaigns_scheduled_touches(db, campaigns)
        meta["finished_at"] = datetime.now(UTC).isoformat()
        logger.info(
            "sequence touches tick finished executed=%s linkedin=%s errors=%s campaigns=%s",
            meta.get("executed"),
            meta.get("linkedin_queued"),
            meta.get("errors"),
            meta.get("campaigns"),
        )
        finish_job_success(db, JOB_SEQUENCE_TOUCHES, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_SEQUENCE_TOUCHES, e)
        raise
    finally:
        db.close()


def run_sourcing_refill_tick() -> dict[str, Any]:
    from app.services.campaign_prospects import count_campaign_prospects
    from app.services.lead_sourcing.auto_bootstrap import (
        _passes_for_remaining,
        process_campaign_sourcing_refill,
        sourcing_refill_enabled,
    )

    if not sourcing_refill_enabled():
        return {"skipped": True, "reason": "sourcing_refill_disabled"}
    if not _automation_engine_active():
        return {"skipped": True, "reason": "automation_disabled"}

    db = SessionLocal()
    try:
        from app.services.provider_guard import sourcing_providers_blocked

        blocked, block_reason = sourcing_providers_blocked(db)
        if blocked:
            return {"skipped": True, "reason": "provider_quota_guard", "detail": block_reason}

        if try_acquire_job(db, JOB_SOURCING_REFILL, lock_ttl_seconds=_DEFAULT_LOCK_SOURCING_REFILL) is None:
            return {"skipped": True, "reason": "locked"}
        campaigns = _active_campaigns(db)
        refilled = 0
        attempted = 0
        imported_total = 0
        errors: list[str] = []
        per_campaign: list[dict[str, Any]] = []
        max_campaigns = int(os.getenv("NEXUS_SOURCING_REFILL_MAX_CAMPAIGNS_PER_TICK", "2"))
        for c in campaigns:
            if refilled >= max_campaigns:
                break
            from app.services.manual_sequence_kickoff import is_individual_container_campaign

            if is_individual_container_campaign(c):
                continue
            target = int(c.prospect_count or 0)
            if target <= 0:
                continue
            current = count_campaign_prospects(db, c.id)
            if current >= target:
                continue
            try:
                remaining = max(0, target - current)
                passes = _passes_for_remaining(remaining) if remaining else 1
                st = process_campaign_sourcing_refill(
                    db, c, max_pipeline_passes=passes
                )
                imported_now = int(st.get("imported") or 0)
                if st.get("ran") and imported_now > 0:
                    refilled += 1
                    imported_total += imported_now
                    if st.get("message"):
                        mseq._append_log(c, str(st["message"]), kind="sourcing")
                elif st.get("ran"):
                    attempted += 1
                    if st.get("message"):
                        mseq._append_log(c, str(st["message"]), kind="sourcing")
                # Persistir la importación antes del día 1: un fallo de kickoff
                # (Gmail/OpenAI/LinkedIn) no debe descartar los prospectos hallados.
                db.commit()

                if imported_now > 0 and (c.status or "") == "running" and not c.automation_paused:
                    try:
                        from app.models.user import User
                        from app.services.campaign_day1_assisted import (
                            kickoff_assisted_day1_for_campaign,
                        )

                        seller = db.get(User, int(c.seller_id)) if c.seller_id else None
                        assisted = kickoff_assisted_day1_for_campaign(db, c, actor=seller)
                        queued_li = int(assisted.get("queued_linkedin") or 0)
                        if queued_li > 0:
                            mseq._append_log(
                                c,
                                f"LinkedIn día 1 en cola: {queued_li} prospecto(s).",
                                kind="linkedin_suggested",
                            )
                        if assisted.get("hold_message"):
                            mseq._append_log(
                                c,
                                str(assisted["hold_message"]),
                                kind="integration_block",
                            )
                        started_n = int(assisted.get("started") or 0)
                        if started_n > 0 and queued_li == 0:
                            mseq._append_log(
                                c,
                                f"Secuencia iniciada para {started_n} prospecto(s) importado(s).",
                                kind="sequence",
                            )
                        db.commit()
                    except Exception as kick_exc:  # noqa: BLE001
                        db.rollback()
                        logger.warning(
                            "post-refill day1 kickoff failed campaign=%s: %s",
                            c.id,
                            str(kick_exc)[:300],
                        )
                per_campaign.append(
                    {
                        "campaign_id": c.id,
                        "imported": st.get("imported"),
                        "after": st.get("prospect_count_after"),
                        "target": st.get("prospect_count_target"),
                        "quota_met": st.get("quota_met"),
                        "ran": st.get("ran"),
                        "ok": st.get("ok"),
                    }
                )
            except Exception as exc:
                db.rollback()
                errors.append(f"campaign={c.id}: {type(exc).__name__}: {exc}"[:500])
        meta = {
            "campaigns_considered": len(campaigns),
            "campaigns_refilled": refilled,
            "campaigns_attempted": attempted,
            "imported": imported_total,
            "per_campaign": per_campaign[:12],
            "errors": errors[:12],
            "finished_at": datetime.now(UTC).isoformat(),
        }
        finish_job_success(db, JOB_SOURCING_REFILL, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_SOURCING_REFILL, e)
        raise
    finally:
        db.close()


def run_plan_credit_renewal_tick() -> dict[str, Any]:
    """Renovación mensual de créditos del plan comercial (1 ciclo por mes calendario)."""
    if not _automation_engine_active():
        return {"skipped": True, "reason": "automation_disabled"}

    from app.services.credits import renew_due_plan_credits

    db = SessionLocal()
    try:
        if try_acquire_job(db, JOB_PLAN_RENEWAL, lock_ttl_seconds=_DEFAULT_LOCK_PLAN_RENEWAL) is None:
            return {"skipped": True, "reason": "locked"}
        meta = renew_due_plan_credits(db)
        db.commit()
        meta["finished_at"] = datetime.now(UTC).isoformat()
        finish_job_success(db, JOB_PLAN_RENEWAL, meta=meta)
        return meta
    except BaseException as e:
        db.rollback()
        finish_job_error(db, JOB_PLAN_RENEWAL, e)
        raise
    finally:
        db.close()


def run_crm_exclusions_sync_tick() -> dict[str, Any]:
    """Pull periódico de cuentas ya contactadas desde HubSpot/Salesforce → exclusiones Nexus."""
    if not _automation_engine_active():
        return {"skipped": True, "reason": "automation_disabled"}

    from app.services.crm import exclusions as crm_exclusions

    db = SessionLocal()
    try:
        if try_acquire_job(
            db, JOB_CRM_EXCLUSIONS, lock_ttl_seconds=_DEFAULT_LOCK_CRM_EXCLUSIONS
        ) is None:
            return {"skipped": True, "reason": "locked"}
        meta = crm_exclusions.sync_exclusions_all_companies(db)
        meta["finished_at"] = datetime.now(UTC).isoformat()
        logger.info(
            "crm exclusions tick finished companies=%s",
            meta.get("companies"),
        )
        finish_job_success(db, JOB_CRM_EXCLUSIONS, meta=meta)
        return meta
    except BaseException as e:
        finish_job_error(db, JOB_CRM_EXCLUSIONS, e)
        raise
    finally:
        db.close()


def run_crm_outbound_retry_tick() -> dict[str, Any]:
    """Reintenta empujar actividades Nexus pendientes hacia el CRM de cada empresa."""
    if not _automation_engine_active():
        return {"skipped": True, "reason": "automation_disabled"}

    from app.services.crm import exclusions as crm_exclusions
    from app.services.crm import sync as crm_sync

    db = SessionLocal()
    try:
        if try_acquire_job(
            db, JOB_CRM_OUTBOUND, lock_ttl_seconds=_DEFAULT_LOCK_CRM_OUTBOUND
        ) is None:
            return {"skipped": True, "reason": "locked"}
        company_ids = crm_exclusions.companies_with_crm(db)
        retried = 0
        resolved = 0
        for company_id in company_ids:
            out = crm_sync.retry_pending_for_company(db, company_id, limit=40)
            retried += int(out.get("retried") or 0)
            resolved += int(out.get("resolved") or 0)
        db.commit()
        meta = {
            "companies": len(company_ids),
            "retried": retried,
            "resolved": resolved,
            "finished_at": datetime.now(UTC).isoformat(),
        }
        logger.info(
            "crm outbound tick finished companies=%s retried=%s resolved=%s",
            meta["companies"],
            retried,
            resolved,
        )
        finish_job_success(db, JOB_CRM_OUTBOUND, meta=meta)
        return meta
    except BaseException as e:
        db.rollback()
        finish_job_error(db, JOB_CRM_OUTBOUND, e)
        raise
    finally:
        db.close()
