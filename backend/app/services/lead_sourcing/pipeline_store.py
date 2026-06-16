"""Persistencia del pipeline por campaña (SQLite)."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import ValidationError

from app.models.lead_sourcing_pipeline import LeadSourcingPipeline
from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead

_DEFAULT_META: dict = {}


def get_row(db: Session, campaign_id: int) -> LeadSourcingPipeline | None:
    """Solo lectura — no crea fila ni hace flush (rápido para GET)."""
    return db.scalars(
        select(LeadSourcingPipeline).where(LeadSourcingPipeline.campaign_id == campaign_id)
    ).first()


def get_or_create(db: Session, campaign_id: int) -> LeadSourcingPipeline:
    row = get_row(db, campaign_id)
    if row is None:
        row = LeadSourcingPipeline(campaign_id=campaign_id, stage="idle", meta_json=json.dumps(_DEFAULT_META))
        db.add(row)
        db.flush()
    return row


def load_companies(row: LeadSourcingPipeline) -> list[CompanyCandidateRead]:
    raw = row.companies_json
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[CompanyCandidateRead] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        try:
            out.append(CompanyCandidateRead.model_validate(x))
        except ValidationError:
            continue
    return out


def save_companies(row: LeadSourcingPipeline, companies: list[CompanyCandidateRead]) -> None:
    row.companies_json = json.dumps([c.model_dump() for c in companies], ensure_ascii=False)


def load_people(row: LeadSourcingPipeline) -> list[LeadCandidateRead]:
    raw = row.people_json
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[LeadCandidateRead] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        try:
            out.append(LeadCandidateRead.model_validate(x))
        except ValidationError:
            continue
    return out


def save_people(row: LeadSourcingPipeline, people: list[LeadCandidateRead]) -> None:
    row.people_json = json.dumps([p.model_dump() for p in people], ensure_ascii=False)


def load_meta(row: LeadSourcingPipeline) -> dict:
    if not row.meta_json:
        return {}
    try:
        data = json.loads(row.meta_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_meta(row: LeadSourcingPipeline, meta: dict) -> None:
    row.meta_json = json.dumps(meta, ensure_ascii=False)


def set_stage(row: LeadSourcingPipeline, stage: str) -> None:
    row.stage = str(stage)
