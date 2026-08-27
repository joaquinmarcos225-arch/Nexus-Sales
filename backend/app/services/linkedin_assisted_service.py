"""
LinkedIn Assisted Layer — MVP asistido con arquitectura lista para extensión Chrome.

Estados (prospect.linkedin_assist_status):
  suggested → prepared → opened → sent
  (abandon desde opened vuelve a suggested con borrador intacto)

LI-SAFE: sin probe de grado ni abrir perfiles en background. Solo borrador + click humano.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.linkedin_assisted import LinkedInAssistQueueRead, LinkedInAssistTaskRead
from app.services.linkedin_reply_delay import apply_reply_queue_delay, reply_visible_in_queue
from app.services.sdr_outreach_compose import (
    generate_playbook_touch_for_prospect,
    prior_touches_from_history,
)
from app.services.ai_instruction_context import campaign_education_blob
from app.services import followup_engine
from app.services.multichannel_sequence import (
    _append_log,
    _day_index_one_based,
    _product_payload,
    _prospect_payload,
    _update_group_for_prospect,
)

logger = logging.getLogger(__name__)

# Sin abrir perfiles / Voyager / verify 1º. No reactivar probes sin permiso explícito.
LI_SAFE_NO_PROFILE_PROBE = True

STATUS_NONE = "none"
STATUS_SUGGESTED = "suggested"
STATUS_PREPARED = "prepared"
STATUS_OPENED = "opened"
STATUS_SENT = "sent"

# Estado del flujo "Conectar" (solicitud de conexión antes del DM).
CONN_NONE = "none"
CONN_CHECKING = "checking"
CONN_CHECK_QUEUED = "check_queued"
CONN_CHECK_FAILED = "check_failed"  # verificación 1/2/3 no resolvió en el plazo
CONN_INVITE_PENDING = "invite_pending"
CONN_INVITE_SENT = "invite_sent"
CONN_CONNECTED = "connected"
CONN_DECLINED = "declined"
CONN_EXPIRED = "expired"

# Solo 1 perfil en checking activo por empresa → la extensión visita de a uno.
_MAX_ACTIVE_CHECKS_PER_COMPANY = 1
_CONN_VALUES = {
    CONN_NONE,
    CONN_CHECKING,
    CONN_CHECK_QUEUED,
    CONN_CHECK_FAILED,
    CONN_INVITE_PENDING,
    CONN_INVITE_SENT,
    CONN_CONNECTED,
    CONN_DECLINED,
    CONN_EXPIRED,
}
# Estados que la extensión puede reportar (incluye not_connected → pasa a Conectar).
_CONN_REPORTABLE = _CONN_VALUES | {"not_connected"}

# Compose research+IA en background (no colgar POST de conexión / cola).
_draft_compose_inflight: set[int] = set()
_draft_compose_lock = threading.Lock()


def read_connection_status(prospect: Prospect) -> str:
    raw = (getattr(prospect, "linkedin_connection_status", None) or "").strip().lower()
    return raw if raw in _CONN_VALUES else CONN_NONE

_DEMO_LI_PATH_RE = (
    re.compile(r"/in/demo[-_]", re.I),
    re.compile(r"/in/test[-_]", re.I),
    re.compile(r"/in/fake[-_]", re.I),
    re.compile(r"/in/mock[-_]", re.I),
    re.compile(r"/in/sample[-_]", re.I),
    re.compile(r"/in/example", re.I),
)


def is_real_linkedin_profile_url(raw: str | None) -> bool:
    url = (raw or "").strip()
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if "linkedin.com" not in host:
        return False
    path = (parsed.path or "").lower()
    if not path.startswith("/in/") and not path.startswith("/sales/"):
        return False
    if any(rx.search(path) or rx.search(url) for rx in _DEMO_LI_PATH_RE):
        return False
    # Perfiles seed/demo de la app: no ocupan el sondeo real de la extensión.
    if "nexus-demo" in path or "nexus-demo" in url.lower():
        return False
    slug = path.removeprefix("/in/").split("/")[0]
    return bool(slug and len(slug) >= 2)


def require_real_linkedin(prospect: Prospect) -> None:
    if not is_real_linkedin_profile_url(prospect.linkedin_url):
        raise ValueError(
            "Este prospecto no tiene un perfil LinkedIn real configurado. "
            "Agregá linkedin.com/in/... válido."
        )


def read_assist_status(prospect: Prospect) -> str:
    raw = (getattr(prospect, "linkedin_assist_status", None) or "").strip().lower()
    if raw in {STATUS_SUGGESTED, STATUS_PREPARED, STATUS_OPENED, STATUS_SENT}:
        return raw
    if getattr(prospect, "linkedin_sdr_marked_sent_at", None):
        return STATUS_SENT
    draft = (prospect.linkedin_assisted_draft or "").strip()
    if not draft:
        return STATUS_NONE
    if getattr(prospect, "linkedin_last_assisted_at", None):
        return STATUS_OPENED
    return STATUS_SUGGESTED


def _set_assist_status(prospect: Prospect, status: str) -> None:
    prospect.linkedin_assist_status = status


def _campaign_payload(campaign: Campaign) -> dict[str, str]:
    from app.services.multichannel_sequence import _campaign_payload as _cp

    return _cp(campaign)


def _conversation_for_prospect(db: Session, prospect_id: int) -> list[OutreachMessage]:
    return list(
        db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect_id)
            .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
        ).all()
    )


def _has_pending_followup(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(func.count())
        .select_from(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.status == "pending",
        )
    )
    return int(n or 0) > 0


def _load_campaign(db: Session, prospect: Prospect) -> Campaign:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(
            selectinload(Campaign.product),
            selectinload(Campaign.seller),
            selectinload(Campaign.company),
        )
    ).first()
    if campaign is None:
        raise ValueError("Campaña no encontrada")
    return campaign


def _log_activity(campaign: Campaign, message: str, *, kind: str) -> None:
    _append_log(campaign, message, kind=kind)


def _priority_for(prospect: Prospect) -> str:
    level = (getattr(prospect, "interest_level", None) or "").lower()
    if level in {"high", "alta"}:
        return "alta"
    if level in {"medium", "media"}:
        return "media"
    group = (prospect.sequence_group or "").lower()
    if group in {"proximo_follow_up", "follow_ups"}:
        return "alta"
    return "baja"


def mark_draft_suggested(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    draft: str,
    *,
    log_event: bool = True,
) -> None:
    """Secuencia o prepare: Nexus sugirió contacto LinkedIn."""
    prospect.linkedin_assisted_draft = draft
    # Reabrir cola tras mark-sent (réplica inbound): sent + marked_sent_at ocultan el item.
    prospect.linkedin_sdr_marked_sent_at = None
    _set_assist_status(prospect, STATUS_SUGGESTED)
    if log_event:
        name = prospect.name or f"Prospecto #{prospect.id}"
        _log_activity(
            campaign,
            f"LinkedIn sugerido · mensaje listo para {name}.",
            kind="linkedin_suggested",
        )


def _li_safe_clear_probe_status(prospect: Prospect) -> bool:
    """Saca checking/queued/failed → none. True si cambió."""
    conn = read_connection_status(prospect)
    if conn in (CONN_CHECKING, CONN_CHECK_QUEUED, CONN_CHECK_FAILED):
        prospect.linkedin_connection_status = CONN_NONE
        return True
    return False


def mark_connection_check_pending(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    *,
    log_event: bool = True,
    pending_draft: str | None = None,
) -> None:
    """
    Histórico: encolaba verify 1º grado vía extensión.

    LI-SAFE: nunca checking. Guarda borrador y deja la tarea en cola de mensaje.
    """
    if LI_SAFE_NO_PROFILE_PROBE:
        draft = (pending_draft or "").strip() or (prospect.linkedin_assisted_draft or "").strip()
        _li_safe_clear_probe_status(prospect)
        if draft:
            mark_draft_suggested(db, prospect, campaign, draft, log_event=log_event)
        else:
            prospect.linkedin_assist_session_id = None
            if read_assist_status(prospect) != STATUS_SENT:
                _set_assist_status(prospect, STATUS_SUGGESTED)
            try:
                schedule_linkedin_quality_draft(int(prospect.id))
            except Exception:
                logger.debug("prefetch linkedin draft on li-safe skip-check failed", exc_info=True)
        return

    # Conservar borrador existente si no pasan uno nuevo (evita borrarlo en re-intentos).
    if pending_draft is not None:
        prospect.linkedin_assisted_draft = (pending_draft or "").strip() or None
    prospect.linkedin_assist_session_id = None
    if read_assist_status(prospect) != STATUS_SENT:
        _set_assist_status(prospect, STATUS_SUGGESTED)

    already = read_connection_status(prospect) == CONN_CHECKING
    if already:
        # No reiniciar el reloj de 120s en cada refresh.
        if pending_draft is not None and (pending_draft or "").strip():
            prospect.linkedin_assisted_draft = (pending_draft or "").strip()
        return

    active = _count_company_checking(db, int(prospect.company_id))
    if active >= _MAX_ACTIVE_CHECKS_PER_COMPANY:
        prospect.linkedin_connection_status = CONN_CHECK_QUEUED
        try:
            schedule_linkedin_quality_draft(int(prospect.id))
        except Exception:
            logger.debug("prefetch linkedin draft on check_queued failed", exc_info=True)
        if log_event:
            name = prospect.name or f"Prospecto #{prospect.id}"
            _log_activity(
                campaign,
                f"LinkedIn · {name} en cola de verificación (de a uno, sin saturar LinkedIn).",
                kind="linkedin_connection_check",
            )
        return

    prospect.linkedin_connection_status = CONN_CHECKING
    # Reloj de verificación: máximo CHECKING_FALLBACK_SECONDS (120s) sin reinicios.
    prospect.linkedin_last_assisted_at = datetime.now(UTC)
    # Research+compose en paralelo al verify → mensaje listo al resolver grado.
    try:
        schedule_linkedin_quality_draft(int(prospect.id))
    except Exception:
        logger.debug("prefetch linkedin draft on checking failed", exc_info=True)
    if log_event:
        name = prospect.name or f"Prospecto #{prospect.id}"
        _log_activity(
            campaign,
            f"LinkedIn · verificando si {name} ya es contacto antes de conectar.",
            kind="linkedin_connection_check",
        )


def _count_company_checking(db: Session, company_id: int) -> int:
    from sqlalchemy import func

    n = db.scalar(
        select(func.count())
        .select_from(Prospect)
        .where(
            Prospect.company_id == int(company_id),
            Prospect.linkedin_connection_status == CONN_CHECKING,
        )
    )
    return int(n or 0)


def normalize_company_connection_checks(db: Session, company_id: int) -> bool:
    """
    Garantiza 1 solo `checking` activo por empresa.
    LI-SAFE: libera todos los checking/queued → none.
    """
    if LI_SAFE_NO_PROFILE_PROBE:
        changed = False
        rows = db.scalars(
            select(Prospect).where(
                Prospect.company_id == int(company_id),
                Prospect.linkedin_connection_status.in_(
                    (CONN_CHECKING, CONN_CHECK_QUEUED, CONN_CHECK_FAILED)
                ),
            )
        ).all()
        for p in rows:
            if _li_safe_clear_probe_status(p):
                changed = True
        return changed
    changed = False
    rows = db.scalars(
        select(Prospect)
        .where(
            Prospect.company_id == int(company_id),
            Prospect.linkedin_connection_status == CONN_CHECKING,
        )
        .order_by(Prospect.id.desc())
    ).all()
    if len(rows) > _MAX_ACTIVE_CHECKS_PER_COMPANY:
        keep = rows[:_MAX_ACTIVE_CHECKS_PER_COMPANY]
        for p in rows[_MAX_ACTIVE_CHECKS_PER_COMPANY:]:
            p.linkedin_connection_status = CONN_CHECK_QUEUED
            changed = True
        for p in keep:
            if getattr(p, "linkedin_last_assisted_at", None) is None:
                p.linkedin_last_assisted_at = datetime.now(UTC)
                changed = True
    if _count_company_checking(db, company_id) < _MAX_ACTIVE_CHECKS_PER_COMPANY:
        if promote_next_linkedin_connection_check(db, int(company_id)) is not None:
            changed = True
    return changed


def promote_next_linkedin_connection_check(db: Session, company_id: int) -> Prospect | None:
    """Pasa el siguiente check_queued → checking (más nuevo primero)."""
    if LI_SAFE_NO_PROFILE_PROBE:
        return None
    if _count_company_checking(db, company_id) >= _MAX_ACTIVE_CHECKS_PER_COMPANY:
        return None
    nxt_rows = db.scalars(
        select(Prospect)
        .where(
            Prospect.company_id == int(company_id),
            Prospect.linkedin_connection_status == CONN_CHECK_QUEUED,
        )
        .order_by(Prospect.id.desc())
        .limit(1)
    ).all()
    nxt = nxt_rows[0] if nxt_rows else None
    if nxt is None:
        return None
    campaign = _load_campaign(db, nxt)
    nxt.linkedin_connection_status = CONN_CHECKING
    nxt.linkedin_last_assisted_at = datetime.now(UTC)
    try:
        schedule_linkedin_quality_draft(int(nxt.id))
    except Exception:
        logger.debug("prefetch linkedin draft on promote failed", exc_info=True)
    if campaign is not None:
        name = nxt.name or f"Prospecto #{nxt.id}"
        _log_activity(
            campaign,
            f"LinkedIn · verificando si {name} ya es contacto antes de conectar.",
            kind="linkedin_connection_check",
        )
    return nxt


def mark_connect_suggested(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    *,
    log_event: bool = True,
    pending_draft: str | None = None,
) -> None:
    """Encola 'Enviar Conectar'. Guarda el DM compuesto para mostrarlo al aceptar (nunca InMail)."""
    prospect.linkedin_connection_status = CONN_INVITE_PENDING
    # Conservamos el borrador del toque para post-aceptación; no se ofrece como InMail.
    draft = (pending_draft or "").strip() or None
    if draft:
        prospect.linkedin_assisted_draft = draft
    elif not (prospect.linkedin_assisted_draft or "").strip():
        prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_session_id = None
    # NO borrar linkedin_last_assisted_at: es el reloj de verificación (evidencia de check).
    if read_assist_status(prospect) != STATUS_SENT:
        _set_assist_status(prospect, STATUS_SUGGESTED)
    if log_event:
        name = prospect.name or f"Prospecto #{prospect.id}"
        note = (
            f"LinkedIn · enviar solicitud de conexión a {name}."
            + (
                " Tras enviar Contactar, el mensaje queda listo en la cola."
                if (prospect.linkedin_assisted_draft or "").strip()
                else ""
            )
        )
        _log_activity(
            campaign,
            note,
            kind="linkedin_connect_suggested",
        )


def queue_linkedin_sequence_touch(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    draft_body: str,
    *,
    log_event: bool = True,
) -> str:
    """
    Decide qué acción de LinkedIn encolar.

    LI-SAFE: siempre 'message' (borrador en bandeja). Sin checking/probe.

    Legacy (LI_SAFE off):
    - Conectados / inbound → Mensaje
    - invite_sent → Mensaje
    - invite_pending → Contactar
    - checking / desconocido → checking

    Devuelve: 'connect' | 'message' | 'hold' | 'skip' | 'checking'.
    """
    from app.services.linkedin_sequence_policy import (
        is_linkedin_connected,
        is_post_connect_draft,
        linkedin_connect_failed,
        mark_post_connect_draft_prepared,
        refresh_linkedin_sequence_state,
    )

    refresh_linkedin_sequence_state(prospect)

    if LI_SAFE_NO_PROFILE_PROBE:
        if linkedin_connect_failed(prospect):
            return "skip"
        _li_safe_clear_probe_status(prospect)
        # Un solo canal asistido vivo: al pasar a LI, WhatsApp sale de la bandeja.
        from app.services.prospect_sequence import _clear_assisted_live_queue

        _clear_assisted_live_queue(prospect, "whatsapp")
        mark_draft_suggested(db, prospect, campaign, draft_body, log_event=log_event)
        return "message"

    if _prospect_has_pending_linkedin_inbound(db, prospect.id):
        mark_draft_suggested(db, prospect, campaign, draft_body, log_event=log_event)
        return "message"

    if is_linkedin_connected(prospect):
        mark_draft_suggested(db, prospect, campaign, draft_body, log_event=log_event)
        return "message"

    status = read_connection_status(prospect)
    if status == CONN_INVITE_SENT and not linkedin_connect_failed(prospect):
        mark_draft_suggested(db, prospect, campaign, draft_body, log_event=log_event)
        if not is_post_connect_draft(prospect):
            mark_post_connect_draft_prepared(prospect)
        return "message"

    if linkedin_connect_failed(prospect):
        return "skip"

    if status == CONN_INVITE_PENDING:
        draft = (draft_body or "").strip()
        if draft:
            prospect.linkedin_assisted_draft = draft
        return "connect"

    if status == CONN_CHECKING:
        draft = (draft_body or "").strip()
        if draft:
            prospect.linkedin_assisted_draft = draft
        return "checking"

    if status == CONN_CHECK_QUEUED:
        draft = (draft_body or "").strip()
        if draft:
            prospect.linkedin_assisted_draft = draft
        return "checking"

    if status == CONN_CHECK_FAILED:
        # Reintentar verificación (no encolar Contactar/Mensaje a ciegas).
        mark_connection_check_pending(
            db,
            prospect,
            campaign,
            log_event=True,
            pending_draft=draft_body,
        )
        return "checking"

    # Desconocido: verificar 1º grado (badge 1er / 2º / 3er) antes de encolar.
    mark_connection_check_pending(
        db,
        prospect,
        campaign,
        log_event=log_event,
        pending_draft=draft_body,
    )
    return "checking"


def mark_connect_sent(db: Session, prospect: Prospect) -> str:
    """El SDR envió Contactar: guarda el DM y lo deja listo en Mensajes (envío humano)."""
    require_real_linkedin(prospect)
    campaign = _load_campaign(db, prospect)
    prospect.linkedin_connection_status = CONN_INVITE_SENT
    prospect.linkedin_invite_sent_at = datetime.now(UTC)
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    # El DM aún no se envió.
    prospect.linkedin_sdr_marked_sent_at = None

    draft = (prospect.linkedin_assisted_draft or "").strip() or None
    if draft and (
        _is_generic_linkedin_stub(draft) or _is_interim_linkedin_draft(draft, prospect)
    ):
        draft = None
        prospect.linkedin_assisted_draft = None
    if draft:
        prospect.linkedin_assisted_draft = draft
        _set_assist_status(prospect, STATUS_SUGGESTED)
        from app.services.linkedin_sequence_policy import mark_post_connect_draft_prepared

        mark_post_connect_draft_prepared(prospect)
        _sync_pending_linkedin_touch_body(db, prospect, draft)
    else:
        if read_assist_status(prospect) not in (STATUS_SUGGESTED, STATUS_SENT, STATUS_OPENED):
            _set_assist_status(prospect, STATUS_SUGGESTED)
        from app.services.linkedin_sequence_policy import mark_post_connect_draft_prepared

        mark_post_connect_draft_prepared(prospect)
        schedule_linkedin_quality_draft(int(prospect.id))

    if prospect.status in {
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
    }:
        prospect.status = ProspectStatus.contacted.value

    name = prospect.name or f"Prospecto #{prospect.id}"
    ready = bool((prospect.linkedin_assisted_draft or "").strip())
    _log_activity(
        campaign,
        (
            f"Solicitud de conexión enviada · {name}. Mensaje listo en cola (envío cuando acepte)."
            if ready
            else f"Solicitud de conexión enviada · {name}. Preparando mensaje LinkedIn."
        ),
        kind="linkedin_connect_sent",
    )
    if ready:
        return (
            "Solicitud registrada. El mensaje quedó en la cola: envialo en LinkedIn cuando acepte."
        )
    return (
        "Solicitud registrada. Estamos preparando el mensaje; aparecerá en la cola cuando esté listo."
    )


def _has_pending_linkedin_sequence_touch(db: Session | None, prospect: Prospect) -> bool:
    try:
        from app.services.prospect_sequence import (
            TOUCH_FALLIDO,
            TOUCH_GENERADO,
            TOUCH_PENDIENTE,
            _playbook_step,
            _planned_days,
            _resolve_campaign,
            _touch_log,
        )

        campaign = None
        if db is not None:
            try:
                campaign = _resolve_campaign(db, prospect)
            except Exception:  # noqa: BLE001
                campaign = None
        log = _touch_log(prospect)
        for day in _planned_days(prospect, campaign):
            entry = log.get(str(day), {})
            status = entry.get("status")
            if status not in (TOUCH_GENERADO, TOUCH_PENDIENTE, TOUCH_FALLIDO):
                continue
            ch = str(entry.get("channel") or "").strip().lower()
            if ch == "linkedin":
                return True
            step = _playbook_step(day, campaign)
            if step is not None and step.channel == "linkedin":
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _sync_pending_linkedin_touch_body(
    db: Session,
    prospect: Prospect,
    body: str | None,
) -> None:
    """Actualiza el toque LinkedIn pendiente con el cuerpo generado post-verificación."""
    text = (body or "").strip() or None
    if not text:
        return
    try:
        from app.services.prospect_sequence import (
            TOUCH_GENERADO,
            _playbook_step,
            _planned_days,
            _resolve_campaign,
            _save_touch_log,
            _touch_log,
        )

        campaign = _resolve_campaign(db, prospect)
        log = _touch_log(prospect)
        changed = False
        for day in _planned_days(prospect, campaign):
            entry = dict(log.get(str(day)) or {})
            if entry.get("status") != TOUCH_GENERADO:
                continue
            step = _playbook_step(day, campaign)
            if step is None or step.channel != "linkedin":
                continue
            entry["message_body"] = text
            entry["body"] = text
            entry.pop("awaiting_connection_check", None)
            entry["error"] = None
            log[str(day)] = entry
            changed = True
            break
        if changed:
            _save_touch_log(prospect, log)
    except Exception:  # noqa: BLE001
        logger.exception(
            "sync pending linkedin touch body failed prospect_id=%s",
            getattr(prospect, "id", None),
        )


def apply_connection_status(db: Session, prospect: Prospect, status: str) -> tuple[str, str | None]:
    """
    La extensión reporta el estado de conexión (1º grado = connected).

    Orden: verificar → encolar acción → dejar mensaje listo (sin bloquear en OpenAI).
    not_connected → Contactar; connected → Mensajes.
    """
    prior = read_connection_status(prospect)
    company_id = int(prospect.company_id or 0)
    try:
        return _apply_connection_status_body(db, prospect, status)
    finally:
        # Liberó un cupo de checking → promover el siguiente de la cola lenta.
        if prior == CONN_CHECKING and company_id:
            promote_next_linkedin_connection_check(db, company_id)


def _apply_connection_status_body(
    db: Session, prospect: Prospect, status: str
) -> tuple[str, str | None]:
    status = (status or "").strip().lower()
    if status not in _CONN_REPORTABLE:
        raise ValueError("Estado de conexión inválido.")
    campaign = _load_campaign(db, prospect)
    name = prospect.name or f"Prospecto #{prospect.id}"

    if status == "not_connected":
        prior = read_connection_status(prospect)
        if prior == CONN_INVITE_SENT:
            return CONN_INVITE_SENT, None
        if prior == CONN_CONNECTED and (
            getattr(prospect, "linkedin_sdr_marked_sent_at", None)
            or read_assist_status(prospect) == STATUS_SENT
        ):
            return CONN_CONNECTED, (prospect.linkedin_assisted_draft or "").strip() or None
        # SOLO desde checking: evidencia de lectura 1/2/3. Cualquier otro estado → (re)verificar.
        if prior != CONN_CHECKING:
            mark_connection_check_pending(
                db,
                prospect,
                campaign,
                log_event=prior not in (CONN_CHECKING, CONN_CHECK_QUEUED),
                pending_draft=(prospect.linkedin_assisted_draft or "").strip() or None,
            )
            return read_connection_status(prospect), None
        existing = (prospect.linkedin_assisted_draft or "").strip() or None
        mark_connect_suggested(
            db,
            prospect,
            campaign,
            log_event=True,
            pending_draft=existing,
        )
        # Post-aceptación: compose en background; Contactar no espera research/IA.
        pending_draft = _queue_ready_linkedin_draft(db, prospect, campaign, existing=existing)
        if pending_draft:
            prospect.linkedin_assisted_draft = pending_draft
            _sync_pending_linkedin_touch_body(db, prospect, pending_draft)
        return CONN_INVITE_PENDING, None

    if status == CONN_CONNECTED:
        prior = read_connection_status(prospect)
        # connected solo con evidencia: checking, o ya en flujo post-Contactar.
        if prior not in (
            CONN_CHECKING,
            CONN_INVITE_PENDING,
            CONN_INVITE_SENT,
            CONN_CONNECTED,
            CONN_CHECK_FAILED,
        ):
            mark_connection_check_pending(
                db,
                prospect,
                campaign,
                log_event=True,
                pending_draft=(prospect.linkedin_assisted_draft or "").strip() or None,
            )
            return read_connection_status(prospect), None
        prospect.linkedin_connection_status = CONN_CONNECTED
        if prospect.linkedin_connected_at is None:
            prospect.linkedin_connected_at = datetime.now(UTC)

        # No plantar stub: research+IA en background; Mensajes aparece cuando esté listo.
        prospect.linkedin_sdr_marked_sent_at = None
        if read_assist_status(prospect) != STATUS_SENT:
            _set_assist_status(prospect, STATUS_SUGGESTED)

        existing = (prospect.linkedin_assisted_draft or "").strip() or None
        draft = _queue_ready_linkedin_draft(db, prospect, campaign, existing=existing)
        prospect.linkedin_assisted_draft = draft or None
        from app.services.linkedin_sequence_policy import mark_post_connect_draft_prepared

        mark_post_connect_draft_prepared(prospect)
        if draft:
            _sync_pending_linkedin_touch_body(db, prospect, draft)
        if prior in (
            CONN_INVITE_SENT,
            CONN_INVITE_PENDING,
            CONN_CHECKING,
            CONN_CHECK_QUEUED,
            CONN_NONE,
        ):
            ready = bool((draft or "").strip())
            _log_activity(
                campaign,
                (
                    f"Conexión detectada · {name}. Mensaje listo para enviar."
                    if ready
                    else f"Conexión detectada · {name}. Preparando mensaje LinkedIn."
                ),
                kind="linkedin_connected",
            )
        return CONN_CONNECTED, (prospect.linkedin_assisted_draft or "").strip() or None

    if status == CONN_DECLINED:
        prospect.linkedin_connection_status = CONN_DECLINED
        _log_activity(
            campaign,
            f"Conexión no concretada · {name}.",
            kind="linkedin_connect_declined",
        )
        return CONN_DECLINED, None

    if status == CONN_INVITE_SENT:
        if read_connection_status(prospect) != CONN_CONNECTED:
            prospect.linkedin_connection_status = CONN_INVITE_SENT
            if prospect.linkedin_invite_sent_at is None:
                prospect.linkedin_invite_sent_at = datetime.now(UTC)
        return read_connection_status(prospect), None

    if status == CONN_CHECKING:
        if read_connection_status(prospect) not in (CONN_CONNECTED, CONN_INVITE_SENT):
            prospect.linkedin_connection_status = CONN_CHECKING
        return read_connection_status(prospect), None

    return read_connection_status(prospect), None


def _is_generic_linkedin_stub(text: str | None) -> bool:
    """Stub rápido post-conexión: no sirve como mensaje real."""
    t = (text or "").strip().lower()
    if not t:
        return True
    # Variantes del stub histórico que el usuario no quiere ver nunca más.
    if "tenés 10 minutos" in t or "tenes 10 minutos" in t:
        return True
    if "tendrías 10 minutos" in t or "tendrias 10 minutos" in t:
        # Solo si el mensaje es casi solo saludo+CTA (sin valor real).
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        if len(lines) <= 4 and "soy " not in t:
            return True
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) <= 3 and "llamada corta" in t and "soy " not in t:
        return True
    return False


def _is_interim_linkedin_draft(
    text: str | None, prospect: Prospect | None = None
) -> bool:
    """Borrador no listo para Enviar: vacío, placeholder o stub genérico."""
    from app.services.prospect_sequence import _is_placeholder_message

    if not (text or "").strip():
        return True
    if _is_placeholder_message(text):
        return True
    if _is_generic_linkedin_stub(text):
        return True
    # CRM floor con research o estructura completa ya es aceptable (no bloquear envío).
    _ = prospect  # reserved for future quality signals
    return False


def _meeting_cta_variants(prospect: Prospect) -> str:
    """CTA siempre orientado a agendar llamada; wording leve variación."""
    variants = (
        "¿Te queda bien una llamada corta esta semana para ver si aplica a tu equipo?",
        "¿Coordinamos una videollamada breve para ver cómo lo implementarían ustedes?",
        "¿Tiene sentido agendar unos minutos para ver si encaja con lo que están haciendo?",
        "¿Podemos coordinar una llamada corta para ver si les suma?",
    )
    idx = int(getattr(prospect, "id", 0) or 0) % len(variants)
    return variants[idx]


def _crm_only_linkedin_draft(prospect: Prospect, campaign: Campaign) -> str:
    """
    Estructura aprobada (primer contacto LinkedIn):

    Hola {Nombre},
    Soy {Sender} de {Empresa}.

    Te escribo porque ayudamos a equipos comerciales a {beneficio}.

    Lo hacemos mediante {Producto}, que {cómo}.

    Esto les permite {resultado}.

    ¿Te interesaría coordinar una reunión breve para mostrarte cómo funciona?
    """
    from app.services.campaign_outreach_context import company_brand_name
    from app.services.openai_fallback import (
        _build_problem_line,
        _product_name,
    )
    from app.services.outreach_display_names import (
        first_real_name_token,
        outreach_company_display,
        sender_first_name,
    )

    first = first_real_name_token(prospect.name or "", fallback="") or ""
    sender = sender_first_name(
        user=getattr(campaign, "seller", None),
        campaign_sender=getattr(campaign, "sender_name", None),
        fallback="el equipo",
    )
    brand = company_brand_name(campaign) or outreach_company_display(
        getattr(getattr(campaign, "company", None), "name", None)
    )
    if not brand:
        brand = "nuestro equipo"

    product = getattr(campaign, "product", None)
    product_dict = {
        "name": (getattr(product, "name", None) or "") if product is not None else "",
        "description": (getattr(product, "description", None) or "") if product is not None else "",
        "value_proposition": (
            (getattr(product, "value_proposition", None) or "") if product is not None else ""
        ),
    }
    product_name = _product_name(product_dict, brand=brand)
    how = re.sub(r"\s+", " ", (product_dict.get("description") or "").strip()).rstrip(".")
    if not how or len(how) < 20:
        how = (
            "automatiza la búsqueda y el contacto por Mail, WhatsApp y LinkedIn "
            "desde un solo lugar"
        )
    else:
        # "X automatiza…" → "automatiza…" para encajar tras «que»
        how_l = how[0].lower() + how[1:] if how else how
        if how_l.lower().startswith(product_name.lower()):
            how_l = how_l[len(product_name) :].lstrip(" ,.-")
        how = how_l

    greeting = f"Hola {first}," if first else "Hola,"
    presentation = f"Soy {sender} de {brand}."
    problem = _build_problem_line(product_dict)
    solution = f"Lo hacemos mediante {product_name}, que {how}."
    if not solution.endswith("."):
        solution = f"{solution}."
    outcome = (
        "Esto les permite reducir el trabajo manual de prospección "
        "y dedicar más tiempo a conversaciones reales."
    )
    cta = "¿Te interesaría coordinar una reunión breve para mostrarte cómo funciona?"

    from app.services.outreach_display_names import scrub_generic_empresa_in_copy

    draft = (
        f"{greeting}\n"
        f"{presentation}\n\n"
        f"{problem}\n\n"
        f"{solution}\n\n"
        f"{outcome}\n\n"
        f"{cta}"
    )
    return scrub_generic_empresa_in_copy(
        draft, prospect_company=getattr(prospect, "company_name", None), brand=brand
    )


def _simple_linkedin_outreach_draft(prospect: Prospect, campaign: Campaign | None = None) -> str:
    """No usar stub corto. Delega a borrador estructurado CRM."""
    if campaign is None:
        first = (prospect.name or "").strip().split()[0] if (prospect.name or "").strip() else ""
        from app.services.outreach_display_names import prospect_company_display

        company = prospect_company_display(prospect.company_name) or "tu equipo"
        greet = f"Hola {first}," if first else "Hola,"
        return (
            f"{greet}\n\n"
            f"Te escribo por tu trabajo en {company}.\n\n"
            "Habitualmente los equipos comerciales pierden horas en prospección manual. "
            "Ayudamos a agendar más reuniones sin ese desgaste.\n\n"
            "¿Te queda bien una llamada corta esta semana para ver si aplica?"
        )
    return _crm_only_linkedin_draft(prospect, campaign)


def schedule_linkedin_quality_draft(prospect_id: int) -> None:
    """Research + compose playbook en hilo daemon (no bloquea HTTP)."""
    pid = int(prospect_id)
    with _draft_compose_lock:
        if pid in _draft_compose_inflight:
            return
        _draft_compose_inflight.add(pid)

    def _run() -> None:
        from app.database.session import SessionLocal
        from app.models.product import Product

        db = SessionLocal()
        try:
            p = db.get(Prospect, pid)
            if p is None:
                return
            if getattr(p, "linkedin_sdr_marked_sent_at", None):
                return
            if read_assist_status(p) == STATUS_SENT:
                return
            if not p.campaign_id:
                return
            try:
                camp = _load_campaign(db, p)
            except ValueError:
                return
            if camp.product_id and getattr(camp, "product", None) is None:
                camp.product = db.get(Product, int(camp.product_id))
            ensure_linkedin_draft(db, p, camp)
            db.commit()
        except Exception:
            logger.exception("linkedin quality draft failed prospect_id=%s", pid)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass
            with _draft_compose_lock:
                _draft_compose_inflight.discard(pid)

    threading.Thread(target=_run, daemon=True, name=f"li-draft-{pid}").start()


def _queue_ready_linkedin_draft(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    *,
    existing: str | None,
) -> str:
    """
    Borrador para la cola YA.
    - Si hay draft bueno → usarlo.
    - Si no → piso CRM inmediato (Contactar/Mensajes visibles) + upgrade IA en background.
    Nunca deja la cola vacía tras resolver el grado.
    """
    from app.services.prospect_sequence import _is_placeholder_message

    text = (existing or "").strip()
    if text and (
        _is_placeholder_message(text)
        or _is_generic_linkedin_stub(text)
    ):
        prospect.linkedin_assisted_draft = None
        text = ""

    if text and not _is_generic_linkedin_stub(text) and not _is_placeholder_message(text):
        _set_assist_status(prospect, STATUS_SUGGESTED)
        # Mejorar en background si aún no hubo research.
        try:
            schedule_linkedin_quality_draft(int(prospect.id))
        except Exception:
            logger.debug("schedule upgrade draft failed", exc_info=True)
        return text

    if getattr(campaign, "product", None) is None and campaign.product_id:
        from app.models.product import Product

        campaign.product = db.get(Product, int(campaign.product_id))

    # Piso inmediato → la cola muestra Mensajes/Contactar sin esperar OpenAI.
    draft = _crm_only_linkedin_draft(prospect, campaign)
    mark_draft_suggested(db, prospect, campaign, draft, log_event=False)
    try:
        schedule_linkedin_quality_draft(int(prospect.id))
    except Exception:
        logger.debug("schedule quality draft after floor failed", exc_info=True)
    return draft


def linkedin_reply_fallback_draft(
    prospect: Prospect,
    campaign: Campaign,
    *,
    inbound_text: str,
) -> str:
    """Compat: delegado al compositor contextual."""
    from app.services.linkedin_reply_compose import linkedin_reply_fallback_draft as _fallback

    return _fallback(prospect, campaign, inbound_text=inbound_text)


def ensure_linkedin_draft(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    draft = (prospect.linkedin_assisted_draft or "").strip()
    from app.services.prospect_sequence import _is_placeholder_message

    if (
        draft
        and not _is_placeholder_message(draft)
        and not _is_interim_linkedin_draft(draft, prospect)
        and not _is_generic_linkedin_stub(draft)
    ):
        return draft
    # Limpiar stub/interim para forzar compose con research.
    if draft and (
        _is_interim_linkedin_draft(draft, prospect) or _is_generic_linkedin_stub(draft)
    ):
        prospect.linkedin_assisted_draft = None
    history = _conversation_for_prospect(db, prospect.id)
    last_inbound = next(
        (m for m in reversed(history) if m.direction == "inbound" and m.channel == "linkedin"),
        None,
    )
    is_reply = last_inbound is not None
    last_text = (last_inbound.message if last_inbound else "") or ""
    if last_text.startswith("[LinkedIn · respuesta real]"):
        last_text = last_text.split("\n", 1)[-1].strip()
    blob = campaign_education_blob(db, campaign)
    if is_reply and last_text.strip():
        from app.services.linkedin_reply_compose import compose_linkedin_inbound_reply
        from app.services.inbound_turn_orchestrator import resolve_inbound_scheduling_reply
        from app.services import conversation_intelligence as ci

        draft = compose_linkedin_inbound_reply(
            db,
            prospect=prospect,
            campaign=campaign,
            inbound_text=last_text,
            history=history,
        )
        prior = (prospect.interest_level or "low").strip() or "low"
        try:
            sig = ci.build_signals_from_keywords(
                ci.normalize_inbound_text_for_classification(last_text),
                prior,
            )
        except Exception:
            sig = None
        response_class = "otro"
        reply_objective = "seguimiento"
        if sig is not None:
            response_class, _ = ci.classify_commercial_response(last_text, sig)
            reply_objective = ci.resolve_reply_objective(
                text=last_text,
                sig=sig,
                response_class=response_class,
            )
        decision = resolve_inbound_scheduling_reply(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=last_text,
            reply_objective=reply_objective,
            sig=sig,
            suggested_reply=draft or "",
            testing=False,
        )
        if decision.action != "skip_autoresponder" and decision.reply_body:
            draft = decision.reply_body
    else:
        prior = prior_touches_from_history(history)
        _subj, draft = generate_playbook_touch_for_prospect(
            db,
            campaign=campaign,
            prospect=prospect,
            education=blob,
            channel="linkedin",
            prior_touches=prior,
        )
    draft = (draft or "").strip()
    if (
        not draft
        or _is_placeholder_message(draft)
        or _is_generic_linkedin_stub(draft)
    ):
        if getattr(campaign, "product", None) is None and campaign.product_id:
            from app.models.product import Product

            campaign.product = db.get(Product, int(campaign.product_id))
        draft = _crm_only_linkedin_draft(prospect, campaign)
    mark_draft_suggested(db, prospect, campaign, draft, log_event=True)
    return draft


def prepare_linkedin_reply_after_inbound(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> str | None:
    """Tras inbound LinkedIn: limpia borrador viejo y genera réplica para la cola SDR."""
    if not is_real_linkedin_profile_url(prospect.linkedin_url):
        return None
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    prospect.linkedin_sdr_marked_sent_at = None
    # Obligatorio: mark-sent deja status=sent y is_queue_eligible lo oculta.
    _set_assist_status(prospect, STATUS_SUGGESTED)
    return ensure_linkedin_draft(db, prospect, campaign)


def regenerate_linkedin_reply_draft(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> str | None:
    """Fuerza nueva réplica LinkedIn (p. ej. tras configurar OpenAI)."""
    if not _prospect_has_pending_linkedin_inbound(db, prospect.id):
        raise ValueError("No hay respuesta LinkedIn pendiente de contestar.")
    return prepare_linkedin_reply_after_inbound(db, prospect, campaign)


def _prospect_has_pending_linkedin_inbound(db: Session, prospect_id: int) -> bool:
    last_out = db.scalar(
        select(OutreachMessage.created_at)
        .where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.channel == "linkedin",
            OutreachMessage.direction == "outbound",
        )
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(1)
    )
    last_in = db.scalar(
        select(OutreachMessage.created_at)
        .where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.channel == "linkedin",
            OutreachMessage.direction == "inbound",
        )
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(1)
    )
    if last_in is None:
        return False
    if last_out is None:
        return True
    return last_in >= last_out


def begin_assist_session(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> tuple[str, str]:
    """
    Inicia sesión asistida. Retorna (draft, session_id).
    NO marca enviado — solo prepara + abre + copia (logs separados).
    """
    require_real_linkedin(prospect)
    had_draft = bool((prospect.linkedin_assisted_draft or "").strip())
    draft = ensure_linkedin_draft(db, prospect, campaign)
    name = prospect.name or f"Prospecto #{prospect.id}"
    now = datetime.now(UTC)

    if not had_draft or read_assist_status(prospect) == STATUS_SUGGESTED:
        _set_assist_status(prospect, STATUS_PREPARED)
        _log_activity(
            campaign,
            f"Mensaje preparado para LinkedIn · {name}.",
            kind="linkedin_prepared",
        )

    session_id = str(uuid.uuid4())
    prospect.linkedin_assist_session_id = session_id
    prospect.linkedin_last_assisted_at = now
    _set_assist_status(prospect, STATUS_OPENED)

    _log_activity(
        campaign,
        f"LinkedIn abierto · {name} (esperando envío manual del SDR).",
        kind="linkedin_opened",
    )
    _log_activity(
        campaign,
        f"Mensaje copiado al portapapeles · {name}.",
        kind="linkedin_copy",
    )
    return draft, session_id


def abandon_assist_session(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    name = prospect.name or f"Prospecto #{prospect.id}"
    if (prospect.linkedin_assisted_draft or "").strip():
        _set_assist_status(prospect, STATUS_SUGGESTED)
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    _log_activity(
        campaign,
        f"LinkedIn sin confirmar envío · {name} (sigue en cola).",
        kind="linkedin_pending",
    )
    return STATUS_SUGGESTED


def confirm_linkedin_sent(db: Session, prospect: Prospect) -> str:
    require_real_linkedin(prospect)
    campaign = _load_campaign(db, prospect)
    draft = (prospect.linkedin_assisted_draft or "").strip()
    if not draft:
        raise ValueError("No hay borrador LinkedIn pendiente para este prospecto.")

    name = prospect.name or f"Prospecto #{prospect.id}"
    body = f"[LinkedIn · enviado por SDR]\n{draft}"

    db.add(
        OutreachMessage(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="user",
            message=body,
            channel="linkedin",
            direction="outbound",
        )
    )

    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=draft,
    )

    if prospect.status in {
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
    }:
        prospect.status = ProspectStatus.contacted.value

    from app.services.linkedin_sequence_policy import (
        clear_post_connect_draft_meta,
        reset_sequence_clock_after_post_connect_dm,
    )

    # Si pudieron mandar el DM, ya son (o actúan como) contacto.
    prospect.linkedin_connection_status = CONN_CONNECTED
    if prospect.linkedin_connected_at is None:
        prospect.linkedin_connected_at = datetime.now(UTC)

    prospect.linkedin_sdr_marked_sent_at = datetime.now(UTC)
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_last_assisted_at = None
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_reply_available_at = None
    _set_assist_status(prospect, STATUS_SENT)

    clear_post_connect_draft_meta(prospect)

    # El toque LinkedIn se completa al enviar el DM (no al mandar Conectar).
    from app.services.prospect_sequence import complete_pending_linkedin_sequence_touch

    complete_pending_linkedin_sequence_touch(db, prospect=prospect)
    sent_at = prospect.linkedin_sdr_marked_sent_at or datetime.now(UTC)
    # Reinicia el reloj de secuencia desde el envío del DM.
    reset_sequence_clock_after_post_connect_dm(prospect, sent_at)

    day = _day_index_one_based(prospect.sequence_started_at)
    _update_group_for_prospect(
        prospect,
        day,
        _has_pending_followup(db, prospect.id),
    )

    _log_activity(
        campaign,
        f"Mensaje confirmado enviado en LinkedIn · {name}.",
        kind="linkedin_sent",
    )

    return "Envío confirmado en LinkedIn."


def is_queue_eligible(prospect: Prospect, db: Session | None = None) -> bool:
    if not is_real_linkedin_profile_url(prospect.linkedin_url):
        return False
    if getattr(prospect, "linkedin_sdr_marked_sent_at", None):
        return False
    if read_assist_status(prospect) == STATUS_SENT:
        return False

    # Conversación activa: se queda en LinkedIn (Responder / paused).
    try:
        from app.services.prospect_sequence import _sequence_held_for_conversation

        held = _sequence_held_for_conversation(prospect)
    except Exception:  # noqa: BLE001
        held = bool(getattr(prospect, "sequence_paused", False))

    campaign = None
    if db is not None:
        try:
            from app.services.prospect_sequence import _resolve_campaign

            campaign = _resolve_campaign(db, prospect)
        except Exception:  # noqa: BLE001
            campaign = None

    if not held:
        # Solo mostrar LI si el proximo toque ejecutable es LinkedIn.
        try:
            from app.services.prospect_sequence import next_executable_channel

            ch = next_executable_channel(prospect, campaign)
            if ch != "linkedin":
                if db is not None and _prospect_has_pending_linkedin_inbound(db, prospect.id):
                    return reply_visible_in_queue(prospect)
                return False
        except Exception:  # noqa: BLE001
            pass

    conn = read_connection_status(prospect)
    # Verificando / falló verificación: cuentan en pending_verify, NO como Contactar/Mensaje.
    if conn in (CONN_CHECKING, CONN_CHECK_QUEUED, CONN_CHECK_FAILED):
        return True
    # Tarea de conexión: solo tras not_connected verificado.
    if conn == CONN_INVITE_PENDING:
        return True
    started = getattr(prospect, "sequence_started_at", None)
    if isinstance(started, datetime) and not _has_pending_linkedin_sequence_touch(db, prospect):
        if db is not None and _prospect_has_pending_linkedin_inbound(db, prospect.id):
            return reply_visible_in_queue(prospect)
        return False
    if not reply_visible_in_queue(prospect):
        return False
    return True


def linkedin_connect_failed_safe(prospect: Prospect) -> bool:
    try:
        from app.services.linkedin_sequence_policy import linkedin_connect_failed

        return bool(linkedin_connect_failed(prospect))
    except Exception:  # noqa: BLE001
        return False


def _task_action(db: Session, prospect: Prospect) -> tuple[str, bool]:
    """Devuelve (action, is_reply) para la tarea de cola."""
    is_reply = _prospect_has_pending_linkedin_inbound(db, prospect.id)
    if LI_SAFE_NO_PROFILE_PROBE:
        # Una sola bandeja: mensaje (+ Contactar humano en UI). Sin verify/connect auto.
        return ("reply" if is_reply else "message"), is_reply
    conn = read_connection_status(prospect)
    if conn in (CONN_CHECKING, CONN_CHECK_QUEUED, CONN_CHECK_FAILED):
        # Aún no sabemos / falló lectura: NUNCA mostrar como Conectar ni Mensaje.
        return "verify_connect", False
    if conn == CONN_INVITE_PENDING:
        return "connect", False
    # invite_sent: DM preparado → Mensajes (el SDR envía cuando acepte; sin re-sondeo).
    return ("reply" if is_reply else "message"), is_reply


def parse_linkedin_fsd_profile_urn(raw: str | None) -> str | None:
    """Extrae el ID ACoAA… desde URN crudo o URL de /messaging/compose."""
    from urllib.parse import unquote, urlparse, parse_qs

    value = (raw or "").strip()
    if not value:
        return None
    m = re.search(r"urn:li:fsd_profile:([A-Za-z0-9_-]+)", value, flags=re.I)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    if "messaging/compose" in value or "profileUrn=" in value or "recipient=" in value:
        try:
            parsed = urlparse(value if "://" in value else f"https://www.linkedin.com/{value.lstrip('/')}")
            qs = parse_qs(parsed.query)
            for key in ("recipient", "profileUrn"):
                for item in qs.get(key) or []:
                    found = parse_linkedin_fsd_profile_urn(unquote(item))
                    if found:
                        return found
        except Exception:
            return None
    return None


def build_linkedin_compose_url(urn: str | None) -> str | None:
    profile_id = parse_linkedin_fsd_profile_urn(urn)
    if not profile_id:
        return None
    encoded = quote(f"urn:li:fsd_profile:{profile_id}", safe="")
    return (
        "https://www.linkedin.com/messaging/compose/"
        f"?profileUrn={encoded}"
        f"&recipient={quote(profile_id, safe='')}"
        "&screenContext=NON_SELF_PROFILE_VIEW"
        "&interop=msgOverlay"
    )


def save_linkedin_profile_urn(
    prospect: Prospect,
    *,
    urn: str | None = None,
    compose_url: str | None = None,
) -> str:
    profile_id = parse_linkedin_fsd_profile_urn(urn) or parse_linkedin_fsd_profile_urn(compose_url)
    if not profile_id:
        raise ValueError("URN de LinkedIn inválido")
    prospect.linkedin_profile_urn = profile_id
    return profile_id


def build_task_read(db: Session, prospect: Prospect) -> LinkedInAssistTaskRead:
    status = read_assist_status(prospect)
    action, is_reply = _task_action(db, prospect)
    conn = read_connection_status(prospect)
    draft = (prospect.linkedin_assisted_draft or "").strip()
    if action == "connect":
        # Conectar: no mostrar el DM (se envía cuando acepten / ya conectados).
        message = ""
        priority = "alta"
    elif action == "verify_connect":
        # Verificando: mostrar preview del mensaje listo (aún no es "Conectar").
        message = draft
        priority = "alta"
    else:
        message = draft
        priority = "alta" if is_reply else _priority_for(prospect)
    return LinkedInAssistTaskRead(
        prospect_id=prospect.id,
        prospect_name=prospect.name or f"Prospecto #{prospect.id}",
        company_name=prospect.company_name,
        linkedin_url=(prospect.linkedin_url or "").strip(),
        linkedin_profile_urn=(getattr(prospect, "linkedin_profile_urn", None) or "").strip() or None,
        message=message,
        assist_status=status,
        session_id=getattr(prospect, "linkedin_assist_session_id", None),
        priority=priority,
        sequence_group=getattr(prospect, "sequence_group", None),
        opened_at=getattr(prospect, "linkedin_last_assisted_at", None),
        suggested_at=getattr(prospect, "created_at", None),
        is_reply=is_reply,
        action=action,
        connection_status=conn,
    )


def linkedin_profile_slug(raw: str | None) -> str | None:
    from urllib.parse import unquote

    url = (raw or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    path = (parsed.path or "").lower()
    if path.startswith("/sales/people/"):
        slug = path.removeprefix("/sales/people/").split("/")[0].strip()
        return unquote(slug) if slug else None
    if not path.startswith("/in/"):
        return None
    slug = path.removeprefix("/in/").split("/")[0].strip()
    return unquote(slug) if slug else None


def resolve_prospect_by_linkedin_url(
    db: Session,
    *,
    company_id: int,
    url: str,
) -> Prospect | None:
    from urllib.parse import unquote
    import unicodedata

    from app.services.lead_sourcing.linkedin_identity import (
        is_personal_linkedin_url,
        normalize_linkedin_url,
    )

    def _slug_key(raw: str | None) -> str:
        s = unquote(str(raw or "")).strip().lower()
        try:
            s = "".join(
                c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
            )
        except Exception:
            pass
        return s

    normalized = normalize_linkedin_url(url)
    if not normalized:
        return None
    target_slug = linkedin_profile_slug(normalized)
    if not target_slug:
        return None
    target_key = _slug_key(target_slug)

    matches: list[Prospect] = []
    rows = db.scalars(select(Prospect).where(Prospect.company_id == company_id)).all()
    for prospect in rows:
        li = (prospect.linkedin_url or "").strip()
        if not li or not is_personal_linkedin_url(li):
            continue
        slug = linkedin_profile_slug(li)
        if slug and _slug_key(slug) == target_key:
            matches.append(prospect)
    if not matches:
        return None

    # Preferir el que acabamos de marcar enviado / con borrador reciente / id más alto
    # (evita registrar inbound en un Mia viejo de otra campaña).
    def _rank(p: Prospect) -> tuple:
        marked = getattr(p, "linkedin_sdr_marked_sent_at", None)
        assist = (getattr(p, "linkedin_assist_status", None) or "").strip().lower()
        return (
            0 if marked is not None else 1,
            0 if assist == "sent" else 1,
            -int(getattr(p, "id", 0) or 0),
        )

    matches.sort(key=_rank)
    return matches[0]


def build_campaign_queue(db: Session, campaign_id: int, viewer=None) -> LinkedInAssistQueueRead:
    from app.services import daily_send_limits as dsl

    campaign = db.get(Campaign, campaign_id)
    seller_id = int(getattr(campaign, "seller_id", 0) or 0)

    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
    if viewer is not None and campaign is not None:
        from app.services.campaign_visibility import filter_prospects_for_viewer

        rows = filter_prospects_for_viewer(viewer, campaign, list(rows))
    tasks: list[LinkedInAssistTaskRead] = []
    from app.services.linkedin_sequence_policy import refresh_linkedin_sequence_state

    state_changed = False
    company_id = int(getattr(campaign, "company_id", 0) or 0)
    if not LI_SAFE_NO_PROFILE_PROBE and company_id and normalize_company_connection_checks(
        db, company_id
    ):
        state_changed = True
    for p in rows:
        if LI_SAFE_NO_PROFILE_PROBE and _li_safe_clear_probe_status(p):
            state_changed = True
        if refresh_linkedin_sequence_state(p):
            state_changed = True
        if LI_SAFE_NO_PROFILE_PROBE and _li_safe_clear_probe_status(p):
            # refresh puede reencolar checking; volver a liberar.
            state_changed = True
        try:
            from app.services.prospect_sequence import expire_unsent_assisted_touches_for_calendar

            expired = expire_unsent_assisted_touches_for_calendar(
                db, prospect=p, campaign=campaign
            )
            if expired:
                state_changed = True
            from app.services.prospect_sequence import ensure_single_assisted_live_queue

            if ensure_single_assisted_live_queue(p, campaign):
                state_changed = True
        except Exception:  # noqa: BLE001
            pass
        conn = read_connection_status(p)
        # Connected / invite_sent sin borrador de calidad → compose en background.
        if (
            conn in (CONN_CONNECTED, CONN_INVITE_SENT)
            and not getattr(p, "linkedin_sdr_marked_sent_at", None)
            and read_assist_status(p) != STATUS_SENT
            and _has_pending_linkedin_sequence_touch(db, p)
        ):
            draft = (getattr(p, "linkedin_assisted_draft", None) or "").strip()
            if not draft or _is_generic_linkedin_stub(draft) or _is_interim_linkedin_draft(
                draft, p
            ):
                if draft and _is_generic_linkedin_stub(draft):
                    p.linkedin_assisted_draft = None
                    state_changed = True
                schedule_linkedin_quality_draft(int(p.id))
        if not is_queue_eligible(p, db):
            continue
        action, _is_reply = _task_action(db, p)
        # Mensajes: no mostrar stubs; vacío sí (grado ya resuelto; copy después).
        if action in ("message", "reply"):
            draft = (getattr(p, "linkedin_assisted_draft", None) or "").strip()
            if draft and (
                _is_generic_linkedin_stub(draft) or _is_interim_linkedin_draft(draft, p)
            ):
                schedule_linkedin_quality_draft(int(p.id))
                continue
            if not draft:
                schedule_linkedin_quality_draft(int(p.id))
        tasks.append(build_task_read(db, p))

    if state_changed:
        db.commit()

    priority_order = {"alta": 0, "media": 1, "baja": 2}
    status_order = {STATUS_OPENED: 0, STATUS_PREPARED: 1, STATUS_SUGGESTED: 2}
    tasks.sort(
        key=lambda t: (
            priority_order.get(t.priority, 9),
            status_order.get(t.assist_status, 9),
            t.prospect_name,
        )
    )

    # Cupo diario por SDR (anti-bloqueo) — repartir en días en lugar de ocultar.
    from app.services import queue_day_schedule as qds
    from app.schemas.linkedin_assisted import LinkedInAssistDayBucket

    invites_limit = dsl.limit_for(dsl.KIND_LINKEDIN_INVITE)
    dms_limit = dsl.limit_for(dsl.KIND_LINKEDIN_DM)
    invites_remaining = dsl.remaining(db, seller_id, dsl.KIND_LINKEDIN_INVITE) if seller_id else invites_limit
    dms_remaining = dsl.remaining(db, seller_id, dsl.KIND_LINKEDIN_DM) if seller_id else dms_limit

    schedulable: list[LinkedInAssistTaskRead] = []
    pending_verify = 0
    for t in tasks:
        if t.action == "verify_connect":
            if not LI_SAFE_NO_PROFILE_PROBE:
                pending_verify += 1
            continue
        schedulable.append(t)

    def _li_kind(task: LinkedInAssistTaskRead) -> str:
        return "connect" if task.action == "connect" else "message"

    day_rows = qds.schedule_dual_budget(
        schedulable,
        classify=_li_kind,
        primary_limit=invites_limit,
        primary_remaining_today=invites_remaining,
        secondary_limit=dms_limit,
        secondary_remaining_today=dms_remaining,
        primary_kinds=frozenset({"connect"}),
    )

    day_buckets: list[LinkedInAssistDayBucket] = []
    for day_offset, day_tasks in day_rows:
        inv_sched = sum(1 for t in day_tasks if t.action == "connect")
        dm_sched = sum(1 for t in day_tasks if t.action != "connect")
        day_buckets.append(
            LinkedInAssistDayBucket(
                day_offset=day_offset,
                label=qds.day_label(day_offset),
                actionable=day_offset == 0,
                invites_limit=invites_limit,
                invites_scheduled=inv_sched,
                dms_limit=dms_limit,
                dms_scheduled=dm_sched,
                tasks=day_tasks,
            )
        )

    hidden = qds.deferred_count(day_rows)

    # Contactar → Mensajes dentro de cada día.
    action_order = {"connect": 0, "reply": 1, "message": 2}
    for bucket in day_buckets:
        bucket.tasks.sort(
            key=lambda t: (
                action_order.get(t.action, 9),
                priority_order.get(t.priority, 9),
                t.prospect_name,
            )
        )
    visible = day_buckets[0].tasks if day_buckets else []

    return LinkedInAssistQueueRead(
        campaign_id=campaign_id,
        tasks=visible,
        total_pending=len(schedulable),
        invites_remaining=invites_remaining,
        invites_limit=invites_limit,
        dms_remaining=dms_remaining,
        dms_limit=dms_limit,
        hidden_by_cap=hidden,
        days=day_buckets,
        pending_verify=pending_verify,
    )


def list_pending_connect_checks(
    db: Session,
    *,
    company_id: int,
    limit: int = 20,
) -> list[dict]:
    """Solo checking: verificar 1º grado. LI-SAFE: vacío + libera checking stuck."""
    if LI_SAFE_NO_PROFILE_PROBE:
        changed = False
        for p in db.scalars(
            select(Prospect).where(Prospect.company_id == int(company_id)).limit(500)
        ).all():
            if _li_safe_clear_probe_status(p):
                changed = True
        if changed:
            db.commit()
        return []

    from app.services.linkedin_sequence_policy import refresh_linkedin_sequence_state

    changed = normalize_company_connection_checks(db, int(company_id))

    rows = db.scalars(
        select(Prospect)
        .where(Prospect.company_id == int(company_id))
        .order_by(Prospect.id.asc())
        .limit(300)
    ).all()
    for p in rows:
        if refresh_linkedin_sequence_state(p):
            changed = True

    # Siempre re-normalizar: refresh puede reencolar/timeout y dejar >1 checking.
    if normalize_company_connection_checks(db, int(company_id)):
        changed = True

    checking_first: list[dict] = []
    for p in db.scalars(
        select(Prospect)
        .where(
            Prospect.company_id == int(company_id),
            Prospect.linkedin_connection_status == CONN_CHECKING,
            Prospect.sequence_started_at.is_not(None),
        )
        .order_by(Prospect.id.desc())
        .limit(5)
    ).all():
        if not is_real_linkedin_profile_url(p.linkedin_url):
            continue
        if not getattr(p, "sequence_started_at", None):
            continue
        camp = db.get(Campaign, int(p.campaign_id)) if p.campaign_id else None
        if camp is None:
            continue
        if (getattr(camp, "status", None) or "").strip().lower() != "running":
            continue
        if bool(getattr(camp, "automation_paused", False)):
            continue
        # Prefetch mensaje mientras la extensión lee el grado.
        try:
            schedule_linkedin_quality_draft(int(p.id))
        except Exception:
            logger.debug("prefetch draft on pending check failed", exc_info=True)
        checking_first.append(
            {
                "prospect_id": p.id,
                "campaign_id": p.campaign_id,
                "prospect_name": p.name or f"Prospecto #{p.id}",
                "linkedin_url": (p.linkedin_url or "").strip(),
                "connection_status": CONN_CHECKING,
            }
        )
    # Hard cap: la extensión solo sondea de a uno.
    out = checking_first[:1]
    if changed:
        db.commit()
    return out


# Compat aliases usados por routes previos
def log_assist_session(db: Session, prospect: Prospect, campaign: Campaign, **kwargs) -> None:
    begin_assist_session(db, prospect, campaign)


def log_assist_abandoned(db: Session, prospect: Prospect, campaign: Campaign) -> None:
    abandon_assist_session(db, prospect, campaign)
