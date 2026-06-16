"""
Curación de tareas de outreach: pocas acciones, ordenadas por impacto, sin duplicar prospecto.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import ProspectStatus
from app.models.outreach_task import OutreachTask

if TYPE_CHECKING:
    from app.models.prospect import Prospect


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Normaliza a UTC aware para comparar con datetime.now(UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _task_score_and_copy(
    t: OutreachTask,
    pr: Prospect | None,
    now: datetime,
) -> tuple[int, str, str, str, str]:
    """
    Devuelve (score, headline, reason, suggested_action, action_label).
    Score más alto = más urgente/valioso.
    """
    kind = t.task_kind or ""
    pr_name = (pr.name if pr else "").strip() or "el prospecto"
    company = (pr.company_name if pr else "").strip()
    camp_name = (t.campaign.name if t.campaign else "").strip() or "la campaña"

    status = (pr.status if pr else "") or ""
    interest = ((pr.interest_level or "").lower() if pr else "")
    meeting_pending = bool(getattr(pr, "meeting_suggestion_pending", False)) if pr else False

    overdue_followup = False
    if kind == "scheduled_followup" and t.due_at is not None:
        due_utc = _ensure_utc(t.due_at)
        if due_utc is not None:
            overdue_followup = due_utc <= now

    # Prioridad base por tipo + señales del prospecto
    score = 10
    reason = "Tarea operativa pendiente."
    suggested = "Revisá la campaña y avanzá el siguiente paso."
    action_label = "Tarea"

    if status == ProspectStatus.meeting_booked.value or (
        pr and interest == "high" and "reunión" in (t.title or "").lower()
    ):
        score = 100
        reason = "Hay señal fuerte de avance hacia reunión o ya agendada."
        suggested = "Confirmá agenda, compartí link y dejá cerrado el próximo paso."
        action_label = "Reunión / agenda"
        headline = f"Priorizar a {pr_name}" + (f" ({company})" if company else "")
        return score, headline, reason, suggested, action_label

    if kind == "hot_lead" or (interest == "high" and kind in {"review_inbound", "awaiting_reply"}):
        score = 88
        reason = "Interés alto detectado; conviene respuesta rápida y concreta."
        suggested = "Proponé llamada breve (10–15 min) y cerrá con una sola pregunta clara."
        action_label = "Lead caliente"
        headline = f"Responder con foco a {pr_name}" + (f" · {company}" if company else "")
        return score, headline, reason, suggested, action_label

    if kind == "deferred_sequence_resume":
        due_utc = _ensure_utc(t.due_at)
        due_txt = ""
        if due_utc is not None:
            due_txt = due_utc.strftime("%Y-%m-%d %H:%M UTC")
        if due_utc is not None and due_utc <= now:
            score = 58
            reason = "Fecha de re-contacto tras postergación: ya venció o es hoy."
        else:
            score = 46
            reason = "Postergación: re-contacto programado para la fecha acordada con el prospecto."
        suggested = "Revisá Gmail y el historial en Nexus; al vencer la tarea, el motor reanuda la secuencia automáticamente."
        action_label = "Re-contacto postergado"
        headline = f"Postergado · {pr_name}" + (f" · {due_txt}" if due_txt else "")
        return score, headline, reason, suggested, action_label

    if kind == "review_inbound":
        score = 72
        reason = "Hay una respuesta entrante que conviene interpretar y contestar con criterio."
        suggested = "Leé el último mensaje, respondé breve y validá interés antes de insistir."
        action_label = "Revisar respuesta"
        headline = f"Revisar conversación con {pr_name}" + (f" ({company})" if company else "")
        return score, headline, reason, suggested, action_label

    if kind == "scheduled_followup" and overdue_followup:
        score = 62
        reason = "Follow-up programado vencido: riesgo de enfriarse el hilo."
        suggested = "Enviá un follow-up corto con un ángulo nuevo (sin repetir el CTA anterior)."
        action_label = "Follow-up vencido"
        headline = f"Follow-up vencido · {pr_name}" + (f" ({company})" if company else "")
        return score, headline, reason, suggested, action_label

    if kind == "awaiting_reply" or meeting_pending:
        score = 55
        reason = "Conversación activa: conviene no dejar pasar demasiado tiempo."
        suggested = "Respondé puntualmente y proponé siguiente paso concreto."
        action_label = "Conversación activa"
        headline = f"Mantener ritmo con {pr_name}" + (f" · {company}" if company else "")
        return score, headline, reason, suggested, action_label

    if kind == "scheduled_followup":
        score = 38
        reason = "Follow-up programado a futuro."
        suggested = "Anticipá el guion y el ángulo para cuando toque enviar."
        action_label = "Follow-up programado"
        headline = f"Planificar seguimiento · {pr_name}" + (f" ({company})" if company else "")
        return score, headline, reason, suggested, action_label

    headline = t.title or f"Acción en {camp_name}"
    action_label = "Tarea"
    return score, headline, reason, suggested, action_label


def load_curated_tasks(
    db: Session,
    *,
    company_id: int,
    campaign_id: int | None = None,
    limit: int,
) -> list[tuple[OutreachTask, str, str, str, str, int]]:
    """
    Devuelve lista de tuplas (task, headline, reason, suggested_action, action_label, score)
    ya filtrada, deduplicada por prospecto y limitada.

    Si `campaign_id` es None, incluye todas las campañas de la empresa.
    """
    now = datetime.now(UTC)
    filter_campaign_id: int | None = campaign_id

    q = (
        select(OutreachTask)
        .where(
            OutreachTask.company_id == company_id,
            OutreachTask.status == "pending",
        )
        .options(selectinload(OutreachTask.campaign), selectinload(OutreachTask.prospect))
    )
    if filter_campaign_id is not None:
        q = q.where(OutreachTask.campaign_id == filter_campaign_id)
    q = q.order_by(OutreachTask.due_at.asc()).limit(200)

    tasks = db.scalars(q).unique().all()
    scored: list[tuple[int, OutreachTask, str, str, str, str]] = []
    for t in tasks:
        pr = t.prospect
        score, headline, reason, suggested, label = _task_score_and_copy(t, pr, now)
        scored.append((score, t, headline, reason, suggested, label))

    def _due_sort(t: OutreachTask) -> datetime:
        d = _ensure_utc(t.due_at)
        if d is None:
            return datetime.min.replace(tzinfo=UTC)
        return d

    scored.sort(key=lambda x: (-x[0], _due_sort(x[1])))

    seen_prospects: set[int] = set()
    out: list[tuple[OutreachTask, str, str, str, str, int]] = []
    for score, t, headline, reason, suggested, label in scored:
        pid = t.prospect_id
        if pid is not None:
            if pid in seen_prospects:
                continue
            seen_prospects.add(pid)
        out.append((t, headline, reason, suggested, label, score))
        if len(out) >= limit:
            break

    return out
