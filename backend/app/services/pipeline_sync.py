"""Sincronía ligera entre status técnico (ProspectStatus) y etapa comercial (PipelineStage)."""

from __future__ import annotations

from app.models.enums import PipelineStage, ProspectStatus

_STAGE_RANK: dict[str, int] = {
    PipelineStage.nuevo.value: 0,
    PipelineStage.contactado.value: 1,
    PipelineStage.respondio.value: 2,
    PipelineStage.interesado.value: 3,
    PipelineStage.reunion_agendada.value: 4,
    PipelineStage.propuesta_enviada.value: 5,
    PipelineStage.negociacion.value: 6,
    PipelineStage.cerrado_ganado.value: 7,
    PipelineStage.cerrado_perdido.value: 7,
}


def rank(stage: str | None) -> int:
    if not stage:
        return -1
    return _STAGE_RANK.get(stage, -1)


def hint_stage_for_status(status: str) -> str | None:
    """Sugerencia de etapa cuando cambia el estado térmico (no fuerza retrocesos)."""
    if status == ProspectStatus.contacted.value:
        return PipelineStage.contactado.value
    if status == ProspectStatus.replied.value:
        return PipelineStage.respondio.value
    if status == ProspectStatus.interested.value:
        return PipelineStage.interesado.value
    if status == ProspectStatus.meeting_booked.value:
        return PipelineStage.reunion_agendada.value
    if status == ProspectStatus.not_interested.value:
        return PipelineStage.cerrado_perdido.value
    if status in (
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.not_compatible.value,
        ProspectStatus.failed.value,
    ):
        return PipelineStage.nuevo.value
    return None


def sync_pipeline_from_status(prospect, *, force: bool = False) -> None:
    """Ajusta pipeline_stage si el nuevo estado lo amerita."""
    st = prospect.status
    ps = getattr(prospect, "pipeline_stage", None) or PipelineStage.nuevo.value
    if st == ProspectStatus.meeting_booked.value:
        prospect.pipeline_stage = PipelineStage.reunion_agendada.value
        return
    if st == ProspectStatus.not_interested.value:
        prospect.pipeline_stage = PipelineStage.cerrado_perdido.value
        return
    hint = hint_stage_for_status(st)
    if hint is None:
        return
    if force or rank(hint) > rank(ps):
        prospect.pipeline_stage = hint


KANBAN_COLUMNS: list[dict[str, object]] = [
    {"id": "nuevo", "label": "Nuevo", "stages": [PipelineStage.nuevo.value]},
    {"id": "contactado", "label": "Contactado", "stages": [PipelineStage.contactado.value]},
    {"id": "respondio", "label": "Respondió", "stages": [PipelineStage.respondio.value]},
    {"id": "interesado", "label": "Interesado", "stages": [PipelineStage.interesado.value]},
    {"id": "reunion", "label": "Reunión", "stages": [PipelineStage.reunion_agendada.value]},
    {
        "id": "negociacion",
        "label": "Negociación",
        "stages": [PipelineStage.negociacion.value, PipelineStage.propuesta_enviada.value],
    },
    {"id": "ganado", "label": "Ganado", "stages": [PipelineStage.cerrado_ganado.value]},
    {"id": "perdido", "label": "Perdido", "stages": [PipelineStage.cerrado_perdido.value]},
]


def kanban_column_for_stage(pipeline_stage: str) -> str:
    for col in KANBAN_COLUMNS:
        if pipeline_stage in col["stages"]:
            return str(col["id"])
    return "nuevo"


def first_stage_for_kanban_column(column_id: str) -> str:
    for col in KANBAN_COLUMNS:
        if col["id"] == column_id:
            stages = col["stages"]
            if isinstance(stages, list) and stages:
                return str(stages[0])
    return PipelineStage.nuevo.value
