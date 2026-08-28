"""Sourcing automático — importar hasta el cupo prospect_count de la campaña."""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services.campaign_prospects import count_campaign_prospects
from app.services.lead_sourcing import pipeline_store as store
from app.services.lead_sourcing import service as ls_service
from app.services.lead_sourcing.env_config import getenv, refresh_lead_sourcing_env
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError

_logger = logging.getLogger(__name__)


def _persist_prospect(db: Session, campaign: Campaign, payload):
    from datetime import UTC, datetime

    from app.models.enums import ProspectOwnershipStatus
    from app.models.user import User
    from app.routes.prospects import _persist_new_prospect

    prospect = _persist_new_prospect(db, campaign, payload)
    # Campañas ICP: asignar al vendedor de la campaña para poder arrancar secuencia.
    if campaign.seller_id and not getattr(prospect, "owner_user_id", None):
        seller = db.get(User, int(campaign.seller_id))
        if seller is not None:
            prospect.owner_user_id = int(seller.id)
            prospect.claimed_at = datetime.now(UTC)
            prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    return prospect


def _importable_external_ids(db: Session, campaign: Campaign) -> list[str]:
    from app.services.lead_sourcing.contact_identity import (
        is_pipeline_contact,
        is_pipeline_generic_contact,
    )
    from app.services.lead_sourcing.icp_import_gate import icp_lead_rank_key
    from app.services.lead_sourcing.prospecting_lead import (
        is_prospecting_importable_for_campaign,
    )
    from app.services.prospect_ingestion import (
        find_duplicate_in_company,
        normalize_linkedin_url,
    )

    row = store.get_or_create(db, campaign.id)
    people = store.load_people(row)
    existing = {
        str(r).strip()
        for r in db.scalars(
            select(Prospect.source_external_id).where(
                Prospect.campaign_id == campaign.id,
                Prospect.source_external_id.isnot(None),
            )
        ).all()
        if r
    }
    ranked: list[tuple[tuple[int, int, int], str]] = []
    for person in people:
        eid = (person.external_id or "").strip()
        if not eid or eid in existing:
            continue
        if not is_pipeline_contact(person) and not is_pipeline_generic_contact(person):
            continue
        if not is_prospecting_importable_for_campaign(
            person, campaign, fit_threshold=row.fit_threshold
        ):
            continue
        if not (person.linkedin_url or person.email):
            continue
        # Ya está en otra campaña de la misma empresa → no cuenta como importable.
        if find_duplicate_in_company(
            db,
            company_id=int(campaign.company_id),
            linkedin_url=normalize_linkedin_url(person.linkedin_url),
            email=person.email,
            phone=getattr(person, "phone", None),
            whatsapp=getattr(person, "whatsapp", None),
        ) is not None:
            continue
        ranked.append((icp_lead_rank_key(person, campaign), eid))
    # Perfectos primero, luego casi perfectos; dentro de cada tier, mejor score.
    ranked.sort(key=lambda x: x[0])
    return [eid for _, eid in ranked]


def _import_batch(db: Session, campaign: Campaign, max_slots: int) -> dict[str, Any]:
    if max_slots <= 0:
        return {"imported": 0, "skipped_duplicates": 0, "errors": [], "prospect_ids": []}
    external_ids = _importable_external_ids(db, campaign)[:max_slots]
    if not external_ids:
        return {"imported": 0, "skipped_duplicates": 0, "errors": [], "prospect_ids": []}
    import_result = ls_service.import_people(
        db,
        campaign,
        external_ids,
        persist_fn=_persist_prospect,
    )
    db.flush()
    return {
        "imported": int(import_result.imported or 0),
        "skipped_duplicates": int(import_result.skipped_duplicates or 0),
        "errors": list(import_result.errors or []),
        "prospect_ids": [int(x) for x in (import_result.prospect_ids or []) if x],
    }


def _maybe_kickoff_new_prospects(
    db: Session,
    campaign: Campaign,
    prospect_ids: list[int],
) -> dict[str, Any] | None:
    """Tras importar: arrancar secuencia de esos prospectos si la campaña está en marcha."""
    ids = [int(x) for x in (prospect_ids or []) if x]
    if not ids:
        return None
    if (campaign.status or "") != "running" or bool(getattr(campaign, "automation_paused", False)):
        return None
    from app.models.user import User
    from app.services import multichannel_sequence as mseq
    from app.services.campaign_day1_assisted import kickoff_assisted_day1_for_prospects

    seller = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
    try:
        # Persistir import ANTES del kickoff: un fallo de LinkedIn/Gmail no descarta prospectos.
        db.commit()
        assisted = kickoff_assisted_day1_for_prospects(
            db, campaign, actor=seller, prospect_ids=ids
        )
        started = int(assisted.get("started") or 0)
        queued_li = int(assisted.get("queued_linkedin") or 0)
        omitted = int(assisted.get("omitted_to_next") or 0)
        held = int(assisted.get("held") or 0)
        parts: list[str] = []
        if started or queued_li:
            parts.append(
                f"Secuencia iniciada para {started or queued_li} prospecto(s) nuevo(s)"
            )
        if queued_li:
            parts.append(f"LinkedIn en cola: {queued_li}")
        if omitted:
            parts.append(f"{omitted} día(s) omitido(s) por dato faltante → siguiente canal")
        if held and assisted.get("hold_message"):
            parts.append(str(assisted["hold_message"]))
        elif held:
            parts.append(f"{held} en espera de reconectar integración/extensión")
        if parts:
            mseq._append_log(campaign, " · ".join(parts) + ".", kind="sequence")
        db.commit()
        return assisted
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _logger.warning(
            "[auto-bootstrap] per-import day1 kickoff failed campaign=%s: %s",
            campaign.id,
            str(exc)[:300],
        )
        return {"ok": False, "error": str(exc)[:300]}


def _max_pipeline_passes_default() -> int:
    raw = (getenv("LEAD_SOURCING_AUTO_MAX_PASSES") or os.getenv("LEAD_SOURCING_AUTO_MAX_PASSES") or "12").strip()
    if raw.isdigit():
        return max(1, min(20, int(raw)))
    return 12


def _effective_passes_limit(
    remaining_slots: int,
    *,
    max_pipeline_passes: int | None = None,
) -> int:
    """Más cupo pendiente → más pasadas hasta un tope razonable."""
    need = max(0, int(remaining_slots))
    if need <= 0:
        return 0
    base = (
        max_pipeline_passes
        if max_pipeline_passes is not None
        else _max_pipeline_passes_default()
    )
    # ~2 pasadas por prospecto faltante (search + enrich + import).
    scaled = max(base, min(20, need * 2))
    return max(1, scaled)


def _pipeline_company_count(db: Session, campaign_id: int) -> int:
    """Cuenta empresas en el pipeline store (0 si no hay fila / error)."""
    try:
        row = store.get_or_create(db, campaign_id)
        return len(store.load_companies(row) or [])
    except Exception:  # noqa: BLE001
        return 0


def _enrich_progress_snapshot(db: Session, campaign_id: int) -> dict[str, Any]:
    """Cursor de enrich Prospeo (para no reiniciar el lote en cada pasada)."""
    try:
        row = store.get_or_create(db, campaign_id)
        meta = store.load_meta(row)
        ep = meta.get("enrich_progress") if isinstance(meta, dict) else None
        if not isinstance(ep, dict):
            return {"has_more": False, "processed": 0, "total": 0, "lote_done": False}
        processed = int(ep.get("processed") or ep.get("cursor") or 0)
        total = int(ep.get("total") or 0)
        has_more = bool(ep.get("has_more"))
        lote_done = (not has_more) and processed > 0 and (total <= 0 or processed >= total)
        return {
            "has_more": has_more,
            "processed": processed,
            "total": total,
            "lote_done": lote_done,
        }
    except Exception:  # noqa: BLE001
        return {"has_more": False, "processed": 0, "total": 0, "lote_done": False}


def _passes_for_remaining(remaining_slots: int, *, hard_cap: int | None = None) -> int:
    """Más cupo pendiente → más pasadas (ritmo continuo por tick / background)."""
    need = max(0, int(remaining_slots))
    if need <= 0:
        return 0
    suggested = max(3, min(15, need))
    cap = hard_cap if hard_cap is not None else _max_pipeline_passes_default()
    return max(1, min(max(cap, 15), suggested))


def _run_mvp_pipeline(db: Session, campaign: Campaign, *, remaining_slots: int = 40) -> dict[str, Any]:
    from app.services.lead_sourcing.sourcing_route import (
        campaign_has_role_icp,
        campaign_uses_role_first_sourcing,
    )
    from app.services.lead_sourcing.prospeo_contact_validation import is_prospeo_searchable_domain

    # Escalar búsqueda al cupo pendiente (over-fetch search barato; import corta al cupo).
    need = max(1, int(remaining_slots))
    company_limit = min(60, max(20, need))
    # Pedir ~2.5× cupo pendiente en search; el import gate deja solo los buenos.
    people_limit = min(100, max(40, need * 3))
    raw_limit = (getenv("LEAD_SOURCING_AUTO_COMPANY_LIMIT") or "").strip()
    if raw_limit.isdigit():
        company_limit = max(1, min(80, int(raw_limit)))

    campaign_has_role = campaign_has_role_icp(campaign)

    if campaign_uses_role_first_sourcing(campaign):
        step = "people_direct"
    else:
        # Empresa→rol: continuar enrich del lote; si el lote murió → nuevo full.
        # Si Brave está sin cuota, no tiene sentido full/companies: ir a Prospeo rol+industria.
        from app.services.lead_sourcing.providers.web_search_backends import (
            brave_quota_exhausted,
        )

        step = "full"
        if brave_quota_exhausted() and campaign_has_role:
            step = "people_direct"
            _logger.info(
                "[auto-bootstrap] campaign=%s Brave quota exhausted → people_direct",
                campaign.id,
            )
        else:
            n_companies = _pipeline_company_count(db, int(campaign.id))
            row = store.get_or_create(db, int(campaign.id))
            meta = store.load_meta(row)
            force_full = bool(meta.get("quota_force_full"))
            if force_full and not brave_quota_exhausted():
                step = "full"
            elif force_full and brave_quota_exhausted() and campaign_has_role:
                step = "people_direct"
            elif n_companies > 0:
                snap = _enrich_progress_snapshot(db, int(campaign.id))
                searchable = 0
                try:
                    for c in store.load_companies(row) or []:
                        if is_prospeo_searchable_domain(getattr(c, "company_domain", None)):
                            searchable += 1
                except Exception:  # noqa: BLE001
                    searchable = 0
                importable = len(_importable_external_ids(db, campaign))
                # Pool muerto: sin importables y pocas empresas con dominio → buscar más empresas.
                pool_dead = importable == 0 and (
                    searchable < max(3, need) or snap.get("lote_done") or not snap.get("has_more")
                )
                if pool_dead:
                    if brave_quota_exhausted() and campaign_has_role:
                        step = "people_direct"
                    else:
                        step = "full"
                        meta["quota_force_full"] = True
                        store.save_meta(row, meta)
                        db.commit()
                elif snap.get("has_more"):
                    step = "enrich"
                elif int(snap.get("processed") or 0) == 0 and not snap.get("lote_done"):
                    step = "enrich"
                # else: lote agotado → full (rota empresas)

    try:
        run = ls_service.run_pipeline_step(
            db,
            campaign,
            step,
            company_limit=company_limit,
            people_limit=people_limit,
        )
    except (ProviderNotConfiguredError, ProviderAPIError, ValueError) as e:
        _logger.warning("[auto-bootstrap] pipeline failed campaign=%s step=%s: %s", campaign.id, step, e)
        # Timeout / error de un paso no debe matar el loop de cupo.
        msg = str(e)
        recoverable = "timeout" in msg.lower() or "rate limit" in msg.lower()
        return {
            "ok": recoverable,
            "message": msg,
            "step": step,
            "recoverable_error": True,
        }
    msg = run.message or ""
    if not run.ok:
        recoverable = "timeout" in msg.lower() or "rate limit" in msg.lower()
        # Tras timeout en enrich: forzar full en la siguiente pasada.
        if recoverable and step == "enrich":
            try:
                row = store.get_or_create(db, int(campaign.id))
                meta = store.load_meta(row)
                meta["quota_force_full"] = True
                meta.pop("enrich_progress", None)
                store.save_meta(row, meta)
                db.commit()
            except Exception:  # noqa: BLE001
                pass
        return {
            "ok": recoverable,
            "message": msg,
            "step": step,
            "recoverable_error": True,
        }
    return {
        "ok": True,
        "message": msg,
        "step": step,
    }


def auto_source_and_import_until_quota(
    db: Session,
    campaign: Campaign,
    *,
    max_pipeline_passes: int | None = None,
) -> dict[str, Any]:
    """
    Importa prospectos hasta alcanzar campaign.prospect_count:
    1) Contactos ya en el pipeline store de la campaña
    2) Pasadas MVP Web Search + Prospeo (hasta max_pipeline_passes)
    """
    count_before = count_campaign_prospects(db, campaign.id)
    target = max(0, int(campaign.prospect_count or 0))

    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if is_individual_container_campaign(campaign):
        return {
            "ran": False,
            "skipped": True,
            "ok": True,
            "reason": "individual_container",
            "prospect_count_before": count_before,
            "prospect_count_target": target,
            "prospect_count_after": count_before,
            "imported": 0,
            "message": None,
        }

    if target <= 0:
        return {
            "ran": False,
            "skipped": True,
            "ok": True,
            "reason": "no_prospect_quota",
            "prospect_count_before": count_before,
            "prospect_count_target": target,
            "prospect_count_after": count_before,
            "imported": 0,
            "message": None,
        }

    if count_before >= target:
        return {
            "ran": False,
            "skipped": True,
            "ok": True,
            "reason": "quota_met",
            "prospect_count_before": count_before,
            "prospect_count_target": target,
            "prospect_count_after": count_before,
            "imported": 0,
            "quota_met": True,
            "message": f"La campaña ya tiene {count_before} de {target} prospecciones.",
        }

    refresh_lead_sourcing_env()
    from app.services.lead_sourcing.providers.registry import pipeline_ready_for_campaign

    if not pipeline_ready_for_campaign(campaign):
        return {
            "ran": True,
            "skipped": True,
            "ok": True,
            "reason": "sourcing_not_configured",
            "prospect_count_before": count_before,
            "prospect_count_target": target,
            "prospect_count_after": count_before,
            "imported": 0,
            "message": "Sin Brave/Prospeo configurados — no se importaron prospectos nuevos.",
        }

    from app.services.provider_guard import sourcing_providers_blocked

    blocked, block_reason = sourcing_providers_blocked(db)
    if blocked:
        return {
            "ran": False,
            "skipped": True,
            "ok": True,
            "reason": "provider_quota_guard",
            "prospect_count_before": count_before,
            "prospect_count_target": target,
            "prospect_count_after": count_before,
            "imported": 0,
            "message": block_reason or "Sourcing pausado por cuota de proveedor.",
        }

    # Rate limit de search-person: NO cortar el bootstrap.
    # El enrich usa fallback Brave+enrich-person y puede importar igual.
    try:
        from app.services.lead_sourcing.prospeo_api_health import (
            prospeo_rate_limit_cooldown_active,
        )

        row = store.get_or_create(db, campaign.id)
        meta = store.load_meta(row)
        if prospeo_rate_limit_cooldown_active(meta):
            _logger.info(
                "[auto-bootstrap] campaign=%s prospeo search cooldown active; "
                "continuing with web+enrich fallback",
                campaign.id,
            )
    except Exception:  # noqa: BLE001
        pass

    passes_limit = _effective_passes_limit(
        max(0, target - count_before),
        max_pipeline_passes=max_pipeline_passes,
    )
    total_imported = 0
    total_duplicates = 0
    pipeline_runs = 0
    pipeline_ok = True
    last_pipeline_message: str | None = None
    kickoff_started = 0
    kickoff_held = 0

    def _remaining_slots() -> int:
        current = count_campaign_prospects(db, campaign.id)
        return max(0, target - current)

    def _after_import(batch: dict[str, Any]) -> None:
        nonlocal kickoff_started, kickoff_held
        ids = list(batch.get("prospect_ids") or [])
        if int(batch.get("imported") or 0) <= 0 or not ids:
            return
        assisted = _maybe_kickoff_new_prospects(db, campaign, ids)
        if not assisted:
            return
        kickoff_started += int(assisted.get("started") or 0)
        kickoff_held += int(assisted.get("held") or 0)

    batch = _import_batch(db, campaign, _remaining_slots())
    total_imported += int(batch["imported"])
    total_duplicates += int(batch["skipped_duplicates"])
    if int(batch.get("imported") or 0) > 0:
        db.commit()
    _after_import(batch)

    empty_streak = 0
    while _remaining_slots() > 0 and pipeline_runs < passes_limit:
        pipeline_runs += 1
        before_companies = _pipeline_company_count(db, int(campaign.id))
        before_enrich = _enrich_progress_snapshot(db, int(campaign.id))
        before_people = 0
        try:
            before_people = len(store.load_people(store.get_or_create(db, int(campaign.id))) or [])
        except Exception:  # noqa: BLE001
            before_people = 0
        pipe = _run_mvp_pipeline(db, campaign, remaining_slots=_remaining_slots())
        last_pipeline_message = pipe.get("message")
        if not pipe.get("ok"):
            pipeline_ok = False
            # Error duro (config/API): cortar. Timeout recuperable ya marca ok=True.
            break
        if pipe.get("recoverable_error") and pipe.get("step") == "enrich":
            # Enrich se colgó/timeout: forzar full en la próxima vuelta del while.
            try:
                row = store.get_or_create(db, int(campaign.id))
                meta = store.load_meta(row)
                meta["quota_force_full"] = True
                meta.pop("enrich_progress", None)
                store.save_meta(row, meta)
                db.commit()
            except Exception:  # noqa: BLE001
                pass
        after_companies = _pipeline_company_count(db, int(campaign.id))
        after_enrich = _enrich_progress_snapshot(db, int(campaign.id))
        after_people = before_people
        try:
            after_people = len(store.load_people(store.get_or_create(db, int(campaign.id))) or [])
        except Exception:  # noqa: BLE001
            after_people = before_people
        batch = _import_batch(db, campaign, _remaining_slots())
        imported_now = int(batch["imported"])
        total_imported += imported_now
        total_duplicates += int(batch["skipped_duplicates"])
        if imported_now > 0:
            db.commit()
        _after_import(batch)
        grew = after_companies > before_companies
        enrich_moved = int(after_enrich.get("processed") or 0) > int(
            before_enrich.get("processed") or 0
        )
        people_grew = after_people > before_people
        if imported_now <= 0 and not grew and not enrich_moved and not people_grew:
            empty_streak += 1
            # Bajo cupo: seguir más rondas antes de ceder (el tick/background reintenta).
            streak_cap = 3 if _remaining_slots() > 0 else 2
            if empty_streak >= streak_cap:
                break
        else:
            empty_streak = 0

    count_after = count_campaign_prospects(db, campaign.id)
    msg: str | None = None
    if total_imported > 0:
        msg = (
            f"Nexus importó {total_imported} prospecto{'s' if total_imported != 1 else ''} "
            f"({count_after} de {target} prospecciones)."
        )
        if total_duplicates:
            msg += f" Omitidos por duplicado: {total_duplicates}."
        if count_after < target:
            msg += " Nexus sigue buscando hasta completar el cupo."
    elif count_after < target:
        # Distinguir: sin candidatos vs todos ya estánen en otra campaña de la empresa.
        company_dupes = 0
        try:
            from app.services.prospect_ingestion import (
                find_duplicate_in_company,
                normalize_linkedin_url,
            )

            row = store.get_or_create(db, campaign.id)
            for person in store.load_people(row) or []:
                if find_duplicate_in_company(
                    db,
                    company_id=int(campaign.company_id),
                    linkedin_url=normalize_linkedin_url(person.linkedin_url),
                    email=person.email,
                    phone=getattr(person, "phone", None),
                    whatsapp=getattr(person, "whatsapp", None),
                ) is not None:
                    company_dupes += 1
        except Exception:  # noqa: BLE001
            company_dupes = 0
        if company_dupes > 0:
            msg = (
                f"Búsqueda ICP hecha; {count_after} de {target} prospecciones. "
                f"{company_dupes} contacto(s) del pipeline ya estánen en otra campaña "
                "de la empresa — Nexus sigue buscando personas nuevas."
            )
        else:
            msg = (
                f"Búsqueda ICP hecha; {count_after} de {target} prospecciones. "
                "No hay más contactos con email o LinkedIn listos para importar."
            )
        # Si Prospeo estaba en rate limit, el mensaje genérico engaña.
        try:
            row = store.get_or_create(db, campaign.id)
            meta = row.meta_json if isinstance(row.meta_json, dict) else {}
            health = meta.get("prospeo_health") if isinstance(meta, dict) else None
            if isinstance(health, dict) and (
                health.get("rate_limited")
                or "RATE_LIMIT" in str(health.get("error_code") or "").upper()
            ):
                until = health.get("rate_limited_until") or ""
                until_note = f" Reintento automático tras {until}." if until else ""
                msg = (
                    f"Prospeo en rate limit; {count_after} de {target} prospecciones."
                    f"{until_note} Nexus reintenta sola en unos minutos."
                )
        except Exception:  # noqa: BLE001
            pass
        if last_pipeline_message:
            msg += f" ({last_pipeline_message})"

    _logger.info(
        "[auto-bootstrap] campaign=%s before=%s after=%s target=%s imported=%s passes=%s",
        campaign.id,
        count_before,
        count_after,
        target,
        total_imported,
        pipeline_runs,
    )

    return {
        "ran": True,
        "skipped": False,
        "ok": pipeline_ok or total_imported > 0,
        "pipeline_ok": pipeline_ok,
        "pipeline_runs": pipeline_runs,
        "prospect_count_before": count_before,
        "prospect_count_target": target,
        "prospect_count_after": count_after,
        "imported": total_imported,
        "skipped_duplicates": total_duplicates,
        "quota_met": count_after >= target,
        "message": msg,
        "sequence_started": kickoff_started,
        "sequence_held": kickoff_held,
    }


def auto_source_and_import_if_empty(db: Session, campaign: Campaign) -> dict[str, Any]:
    """Alias histórico — importa hasta el cupo de la campaña (no solo si está vacía)."""
    return auto_source_and_import_until_quota(db, campaign)


def sourcing_refill_enabled() -> bool:
    raw = (os.getenv("NEXUS_SOURCING_REFILL_ENABLED") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    # Con scheduler activo las campañas grandes necesitan refill continuo.
    if (os.getenv("NEXUS_AUTOMATION_SCHEDULER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    from app.services import outreach_metrics as om

    return om.is_real_mode()


def process_campaign_sourcing_refill(
    db: Session,
    campaign: Campaign,
    *,
    max_pipeline_passes: int | None = None,
) -> dict[str, Any]:
    """Un intento de refill para campañas activas por debajo del cupo."""
    current = count_campaign_prospects(db, campaign.id)
    target = max(0, int(campaign.prospect_count or 0))
    remaining = max(0, target - current)
    passes = (
        max_pipeline_passes
        if max_pipeline_passes is not None
        else _passes_for_remaining(remaining)
    )
    return auto_source_and_import_until_quota(
        db,
        campaign,
        max_pipeline_passes=max(1, passes),
    )


_bg_sourcing_lock = None
_bg_sourcing_inflight: set[int] = set()


def _bg_lock():
    global _bg_sourcing_lock
    if _bg_sourcing_lock is None:
        import threading

        _bg_sourcing_lock = threading.Lock()
    return _bg_sourcing_lock


def schedule_campaign_sourcing_background(campaign_id: int) -> bool:
    """
    Dispara búsqueda/importación ICP en un hilo daemon (después del commit de start).
    Devuelve False si ya hay un job en vuelo para esa campaña.
    """
    import threading

    lock = _bg_lock()
    with lock:
        if campaign_id in _bg_sourcing_inflight:
            _logger.info("[auto-bootstrap] sourcing already in flight campaign=%s", campaign_id)
            return False
        _bg_sourcing_inflight.add(campaign_id)

    def _worker() -> None:
        from app.database.session import SessionLocal
        from app.services import multichannel_sequence as mseq
        from app.services.ai_instruction_context import campaign_education_blob
        from app.schemas.campaign_channels import coerce_allowed_channels
        from app.services.campaign_day1_assisted import kickoff_assisted_day1_for_campaign
        from app.services.campaign_prospects import count_campaign_prospects
        from app.models.user import User

        db = SessionLocal()
        try:
            campaign = db.get(Campaign, campaign_id)
            if campaign is None:
                return

            count_now = count_campaign_prospects(db, campaign.id)
            target = max(0, int(campaign.prospect_count or 0))
            imported = 0
            sourcing: dict[str, Any] = {"ran": False, "message": None}

            # Seguir buscando mientras no se cumpla el cupo (varias rondas en un solo arranque).
            if target > 0 and count_now < target:
                max_rounds = max(1, min(5, int(os.getenv("NEXUS_SOURCING_BG_MAX_ROUNDS", "4"))))
                round_num = 0
                while count_now < target and round_num < max_rounds:
                    remaining = target - count_now
                    passes = _passes_for_remaining(remaining, hard_cap=15)
                    sourcing = auto_source_and_import_until_quota(
                        db, campaign, max_pipeline_passes=passes
                    )
                    imported += int(sourcing.get("imported") or 0)
                    count_now = count_campaign_prospects(db, campaign.id)
                    if count_now >= target:
                        break
                    round_num += 1
                    if int(sourcing.get("imported") or 0) <= 0 and int(
                        sourcing.get("pipeline_runs") or 0
                    ) <= 0:
                        break
                if count_now < target and not sourcing.get("message"):
                    sourcing = {
                        **sourcing,
                        "message": (
                            f"{count_now} de {target} prospecciones. "
                            "Nexus sigue buscando en segundo plano hasta completar el cupo."
                        ),
                    }
            else:
                sourcing = {
                    "ran": False,
                    "skipped": True,
                    "imported": 0,
                    "message": (
                        f"Prospectos listos ({count_now}). Preparando outreach en segundo plano."
                    ),
                }

            if sourcing.get("message"):
                mseq._append_log(campaign, str(sourcing["message"]), kind="sourcing")

            # Persistir los prospectos importados ANTES del día 1: si el kickoff
            # falla (Gmail/OpenAI/LinkedIn), el rollback no debe borrar la búsqueda.
            db.commit()
            _logger.info(
                "[auto-bootstrap] background sourcing committed campaign=%s count=%s imported=%s",
                campaign_id,
                count_now + imported,
                imported,
            )
        except Exception:  # noqa: BLE001
            db.rollback()
            _logger.exception(
                "[auto-bootstrap] background sourcing failed campaign=%s", campaign_id
            )
            try:
                campaign = db.get(Campaign, campaign_id)
                if campaign is not None:
                    mseq._append_log(
                        campaign,
                        "Error buscando prospectos en segundo plano. Reintentá o revisá Integraciones.",
                        kind="sourcing",
                    )
                    db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()

        # Día 1 en transacción aparte: nunca puede descartar prospectos ya importados.
        try:
            campaign = db.get(Campaign, campaign_id)
            if (
                campaign is not None
                and (campaign.status or "") == "running"
                and not campaign.automation_paused
            ):
                education = campaign_education_blob(db, campaign)
                channels = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
                mseq.bootstrap_on_start(
                    db,
                    campaign,
                    channels_allowed=channels,
                    education_blob=education,
                )
                seller = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
                assisted = kickoff_assisted_day1_for_campaign(db, campaign, actor=seller)
                if int(assisted.get("queued_linkedin") or 0) > 0:
                    mseq._append_log(
                        campaign,
                        f"LinkedIn día 1 en cola: {assisted['queued_linkedin']} prospecto(s).",
                        kind="linkedin_suggested",
                    )
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            _logger.exception(
                "[auto-bootstrap] background day1 kickoff failed campaign=%s", campaign_id
            )
        finally:
            db.close()
            with lock:
                _bg_sourcing_inflight.discard(campaign_id)

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"nexus-sourcing-{campaign_id}",
    ).start()
    return True
