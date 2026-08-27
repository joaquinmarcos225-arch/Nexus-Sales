"""Búsqueda en background de canales faltantes (Prospeo) para insert manual.

Flujo:
1. Al insertar: marcar searching + deadline, devolver HTTP rápido.
2. Hilo daemon: enriquecé respetando deadline; si vence, deja lo hallado.
3. Si la campaña ya está running → kickoff con lo conseguido (día 1 omitible).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus
from app.models.prospect import Prospect

_logger = logging.getLogger(__name__)

# Máximo de espera para Prospeo/Brave antes de arrancar con lo que haya.
# Default 120s: 4 rondas densas (Prospeo∥Brave) bastan; antes 180s con 8×4s idle.
MANUAL_CHANNEL_ENRICH_MAX_SECONDS = max(
    45,
    min(240, int(os.getenv("MANUAL_CHANNEL_ENRICH_MAX_SECONDS", "120") or 120)),
)

STATUS_NONE = "none"
STATUS_SEARCHING = "searching"
STATUS_DONE = "done"
STATUS_TIMED_OUT = "timed_out"
STATUS_SKIPPED = "skipped"

_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def has_enrich_anchor(prospect: Prospect) -> bool:
    """
    Cualquier dato usable para buscar lo que falta:
    LinkedIn, email, WhatsApp/teléfono (+nombre si hace falta), o nombre+empresa.
    """
    from app.services.lead_sourcing.linkedin_identity import (
        is_personal_linkedin_url,
        normalize_linkedin_url,
    )
    from app.services.manual_prospect_channel_enrichment import name_is_searchable

    if is_personal_linkedin_url(normalize_linkedin_url(prospect.linkedin_url)):
        return True
    email = (prospect.email or "").strip()
    if email and "@" in email:
        return True
    phone = (prospect.phone or prospect.whatsapp or "").strip()
    name = (prospect.name or "").strip()
    if phone and name:
        return True
    # Solo teléfono: intentamos igual (Prospeo puede no resolverlo).
    if phone:
        return True
    company = (prospect.company_name or "").strip()
    if name and company and company not in {"—", "-", "n/a", "sin empresa"}:
        return True
    # Nombre + apellido: Brave / search-person pueden resolver LinkedIn.
    if name_is_searchable(name):
        return True
    return False


def begin_manual_channel_enrich(
    db: Session,
    prospect: Prospect,
    *,
    sequence_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Marca el prospecto en búsqueda si faltan canales del plan y hay ancla.
    No llama a Prospeo acá (eso va en background).
    """
    from app.services.manual_prospect_channel_enrichment import (
        _missing_channels,
        channels_needed_from_sequence_plan,
        format_channel_search_message,
    )

    needed = channels_needed_from_sequence_plan(sequence_plan)
    missing = _missing_channels(prospect)
    if needed:
        missing &= needed

    if not missing:
        prospect.channel_enrich_status = STATUS_SKIPPED
        prospect.channel_enrich_deadline_at = None
        prospect.channel_enrich_message = None
        return {
            "enriching": False,
            "status": STATUS_SKIPPED,
            "missing": [],
            "message": None,
            "max_seconds": MANUAL_CHANNEL_ENRICH_MAX_SECONDS,
        }

    if not has_enrich_anchor(prospect):
        prospect.channel_enrich_status = STATUS_SKIPPED
        prospect.channel_enrich_deadline_at = None
        prospect.channel_enrich_message = (
            "Falta un dato para buscar (email, LinkedIn, WhatsApp, o nombre y apellido)."
        )
        return {
            "enriching": False,
            "status": STATUS_SKIPPED,
            "missing": sorted(missing),
            "message": prospect.channel_enrich_message,
            "max_seconds": MANUAL_CHANNEL_ENRICH_MAX_SECONDS,
        }

    msg = format_channel_search_message(missing)
    deadline = _now() + timedelta(seconds=MANUAL_CHANNEL_ENRICH_MAX_SECONDS)
    prospect.channel_enrich_status = STATUS_SEARCHING
    prospect.channel_enrich_deadline_at = deadline
    prospect.channel_enrich_message = msg
    if db is not None:
        db.flush()
    return {
        "enriching": True,
        "status": STATUS_SEARCHING,
        "missing": sorted(missing),
        "message": msg,
        "deadline_at": deadline.isoformat(),
        "max_seconds": MANUAL_CHANNEL_ENRICH_MAX_SECONDS,
    }


def wait_until_enrich_settled(
    db: Session,
    prospect: Prospect,
    *,
    poll_seconds: float = 0.4,
) -> str:
    """
    Espera hasta que deje de estar searching o venza el deadline.
    Si vence y sigue searching, marca timed_out.
    """
    import time

    status = (prospect.channel_enrich_status or STATUS_NONE).strip().lower()
    if status != STATUS_SEARCHING:
        return status or STATUS_NONE

    deadline = _as_utc(prospect.channel_enrich_deadline_at) or (
        _now() + timedelta(seconds=MANUAL_CHANNEL_ENRICH_MAX_SECONDS)
    )
    while True:
        db.refresh(prospect)
        status = (prospect.channel_enrich_status or STATUS_NONE).strip().lower()
        if status != STATUS_SEARCHING:
            return status
        if _now() >= deadline:
            prospect.channel_enrich_status = STATUS_TIMED_OUT
            if not (prospect.channel_enrich_message or "").strip():
                prospect.channel_enrich_message = (
                    "Tiempo de búsqueda agotado; se sigue con los datos disponibles."
                )
            db.flush()
            return STATUS_TIMED_OUT
        time.sleep(poll_seconds)


def run_manual_channel_enrich_job(
    db: Session,
    *,
    prospect_id: int,
    kickoff_if_running: bool = True,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Ejecuta enrich respetando deadline y opcionalmente kickoff."""
    from app.services.manual_prospect_channel_enrichment import (
        enrich_prospect_for_sequence_plan,
    )

    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return {"ok": False, "reason": "missing_prospect"}

    status = (prospect.channel_enrich_status or "").strip().lower()
    if status != STATUS_SEARCHING:
        return {"ok": True, "skipped": True, "status": status or STATUS_NONE}

    campaign = db.get(Campaign, prospect.campaign_id)
    plan = getattr(campaign, "sequence_plan", None) if campaign is not None else None
    if not isinstance(plan, dict):
        plan = None

    deadline = _as_utc(prospect.channel_enrich_deadline_at)

    enrich_result: dict[str, Any] = {}
    try:
        enrich_result = (
            enrich_prospect_for_sequence_plan(
                db,
                prospect,
                sequence_plan=plan,
                deadline_at=deadline,
            )
            or {}
        )
    except Exception as exc:  # noqa: BLE001
        _logger.info("manual channel enrich failed prospect=%s: %s", prospect_id, exc)
        enrich_result = {}

    db.refresh(prospect)
    filled = (enrich_result.get("filled") or {}) if isinstance(enrich_result, dict) else {}
    still = (enrich_result.get("missing_after") or []) if isinstance(enrich_result, dict) else []
    timed_out = bool(enrich_result.get("timed_out")) or (
        deadline is not None and _now() >= deadline
    )

    from app.services.manual_prospect_channel_enrichment import (
        channels_needed_from_sequence_plan,
        format_channel_find_summary,
    )

    needed = channels_needed_from_sequence_plan(plan)
    summary = format_channel_find_summary(
        needed=needed or {"email", "phone", "linkedin"},
        prospect=prospect,
        filled={k: v for k, v in filled.items() if k in ("email", "linkedin", "phone")},
        missing_after=still,
        enrich_status=STATUS_TIMED_OUT if timed_out else STATUS_DONE,
    )

    if timed_out:
        prospect.channel_enrich_status = STATUS_TIMED_OUT
        prospect.channel_enrich_message = summary or (
            "Búsqueda cortada por tiempo; se sigue con los datos que ya tenías."
        )
    else:
        prospect.channel_enrich_status = STATUS_DONE
        prospect.channel_enrich_message = summary or "Sin canales pendientes."

    try:
        from app.services.nexus_contact_cache import safe_upsert_from_prospect

        safe_upsert_from_prospect(db, prospect)
    except Exception:  # noqa: BLE001
        pass

    db.flush()

    kickoff_result: dict[str, Any] | None = None
    # Si la campaña ya está running, arrancar secuencia tras enriquecer
    # (aunque el job se haya disparado antes del "Iniciar").
    actor_id = actor_user_id
    if actor_id is None and campaign is not None and campaign.seller_id:
        actor_id = int(campaign.seller_id)
    running = bool(
        campaign is not None
        and (campaign.status or "").strip().lower() == CampaignStatus.running.value
    )
    if running and prospect.sequence_started_at is None and actor_id:
        try:
            from app.models.user import User
            from app.services.manual_sequence_kickoff import (
                kickoff_individual_sequence_for_prospect,
            )

            actor = db.get(User, int(actor_id))
            if actor is not None:
                kickoff_result = kickoff_individual_sequence_for_prospect(
                    db,
                    actor=actor,
                    campaign=campaign,
                    prospect=prospect,
                    wait_for_enrich=False,
                )
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None) or str(exc)
            _logger.warning(
                "post-enrich kickoff failed prospect=%s: %s",
                prospect_id,
                str(detail)[:300],
            )
            kickoff_result = {"ok": False, "detail": str(detail)[:400]}

    return {
        "ok": True,
        "status": prospect.channel_enrich_status,
        "filled": filled,
        "missing_after": still,
        "timed_out": timed_out,
        "kickoff": kickoff_result,
    }


def schedule_manual_channel_enrich(
    prospect_id: int,
    *,
    actor_user_id: int | None = None,
    kickoff_if_running: bool = True,
) -> bool:
    """Dispara enrich en hilo daemon. False si ya hay job en vuelo."""
    _ = kickoff_if_running  # kickoff se decide por campaign.running al terminar el job
    with _inflight_lock:
        if prospect_id in _inflight:
            return False
        _inflight.add(prospect_id)

    def _worker() -> None:
        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            run_manual_channel_enrich_job(
                db,
                prospect_id=prospect_id,
                kickoff_if_running=True,
                actor_user_id=actor_user_id,
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "manual channel enrich worker crashed prospect=%s: %s", prospect_id, exc
            )
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            try:
                db2 = SessionLocal()
                try:
                    p = db2.get(Prospect, prospect_id)
                    if p is not None and (p.channel_enrich_status or "") == STATUS_SEARCHING:
                        p.channel_enrich_status = STATUS_TIMED_OUT
                        p.channel_enrich_message = (
                            "No se pudo completar la búsqueda; se sigue con los datos disponibles."
                        )
                        db2.commit()
                finally:
                    db2.close()
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()
            with _inflight_lock:
                _inflight.discard(prospect_id)

    threading.Thread(
        target=_worker,
        name=f"manual-channel-enrich-{prospect_id}",
        daemon=True,
    ).start()
    return True


def queue_enrich_for_inserted_prospects(
    db: Session,
    prospects: list[Prospect],
    *,
    sequence_plan: dict[str, Any] | None,
    actor_user_id: int | None = None,
) -> list[int]:
    """Marca searching y programa jobs. Hace commit si hay alguno en búsqueda."""
    ids: list[int] = []
    for prospect in prospects:
        if prospect is None or not getattr(prospect, "id", None):
            continue
        meta = begin_manual_channel_enrich(
            db, prospect, sequence_plan=sequence_plan
        )
        if meta.get("enriching"):
            ids.append(int(prospect.id))
    if ids:
        db.commit()
        for pid in ids:
            schedule_manual_channel_enrich(
                pid,
                actor_user_id=actor_user_id,
                kickoff_if_running=True,
            )
    return ids
