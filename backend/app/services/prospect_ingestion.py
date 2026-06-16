"""
Ingesta de prospectos (manual, bulk CSV-like, simulación y futura extensión Chrome / LinkedIn).

El endpoint bulk está pensado para recibir lotes de candidatos desde automatizaciones
externas manteniendo la misma forma que el alta manual — el backend deduplica por campaña.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.prospect import Prospect


def normalize_linkedin_url(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


def find_duplicate_in_campaign(
    session: Session,
    *,
    campaign_id: int,
    linkedin_url: str | None,
    name: str,
    company_name: str,
    email: str | None = None,
    source_external_id: str | None = None,
) -> Prospect | None:
    """
    Dedup por campaña:
    - Con linkedin_url normalizado: mismo URL (case-insensitive), clave principal para la futura extensión.
    - Sin linkedin_url: mismo nombre + empresa (sin importar si el registro existente tiene URL o no).
    """
    ext = (source_external_id or "").strip()
    if ext:
        hit = session.scalars(
            select(Prospect).where(
                Prospect.campaign_id == campaign_id,
                Prospect.source_external_id == ext,
            )
        ).first()
        if hit is not None:
            return hit

    em = (email or "").strip().lower()
    if em:
        hit = session.scalars(
            select(Prospect).where(
                Prospect.campaign_id == campaign_id,
                func.lower(Prospect.email) == em,
            )
        ).first()
        if hit is not None:
            return hit

    li = normalize_linkedin_url(linkedin_url)
    if li:
        key = li.lower()
        stmt = select(Prospect).where(
            Prospect.campaign_id == campaign_id,
            func.lower(Prospect.linkedin_url) == key,
        )
        return session.scalars(stmt).first()

    nm = name.strip().lower()
    cm = company_name.strip().lower()
    stmt = select(Prospect).where(
        Prospect.campaign_id == campaign_id,
        func.lower(Prospect.name) == nm,
        func.lower(Prospect.company_name) == cm,
    )
    return session.scalars(stmt).first()
