"""
Ingesta de prospectos (manual, bulk CSV-like, simulación y futura extensión Chrome / LinkedIn).

El endpoint bulk está pensado para recibir lotes de candidatos desde automatizaciones
externas manteniendo la misma forma que el alta manual — deduplica por campaña y por empresa
(email / slug LinkedIn / teléfono usable). Misma empresa = misma persona una sola vez.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.prospect import Prospect
from app.services.lead_sourcing.linkedin_identity import (
    linkedin_slug_key,
    normalize_linkedin_url as normalize_personal_linkedin_url,
)
from app.services.whatsapp_cloud_service import (
    meta_api_recipient_candidates,
    normalize_whatsapp_digits,
)


def normalize_linkedin_url(value: str | None) -> str | None:
    strong = normalize_personal_linkedin_url(value)
    if strong:
        return strong
    if value is None:
        return None
    v = value.strip()
    return v or None


def phone_identity_keys(phone: str | None = None, whatsapp: str | None = None) -> set[str]:
    """Dígitos comparables (incluye variantes AR 549/5411)."""
    keys: set[str] = set()
    for raw in (phone, whatsapp):
        digits = normalize_whatsapp_digits(raw, None)
        if digits:
            keys.add(digits)
        for cand in meta_api_recipient_candidates(raw, None):
            if cand:
                keys.add(cand)
    return keys


def company_contact_exclusion_sets(
    session: Session,
    company_id: int,
) -> tuple[set[str], set[str], set[str]]:
    """Emails, slugs LinkedIn y teléfonos ya presentes en la empresa (todas las campañas)."""
    emails: set[str] = set()
    slugs: set[str] = set()
    phones: set[str] = set()
    rows = session.execute(
        select(
            Prospect.email,
            Prospect.linkedin_url,
            Prospect.phone,
            Prospect.whatsapp,
        ).where(Prospect.company_id == int(company_id))
    ).all()
    for em, li, phone, wa in rows:
        if em and "@" in str(em):
            emails.add(str(em).strip().lower())
        slug = linkedin_slug_key(li)
        if slug:
            slugs.add(slug)
        phones |= phone_identity_keys(phone, wa)
    return emails, slugs, phones


def _row_phone_keys(row: Prospect) -> set[str]:
    return phone_identity_keys(row.phone, row.whatsapp)


def find_duplicate_in_campaign(
    session: Session,
    *,
    campaign_id: int,
    linkedin_url: str | None,
    name: str,
    company_name: str,
    email: str | None = None,
    source_external_id: str | None = None,
    phone: str | None = None,
    whatsapp: str | None = None,
) -> Prospect | None:
    """
    Dedup por campaña:
    - Con linkedin_url: mismo slug (host/query/encoding no importan).
    - Email / teléfono usable.
    - Sin identidad: mismo nombre + empresa.
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

    slug = linkedin_slug_key(linkedin_url)
    incoming_phones = phone_identity_keys(phone, whatsapp)
    if slug or incoming_phones:
        rows = session.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
        for row in rows:
            if slug and linkedin_slug_key(row.linkedin_url) == slug:
                return row
            if incoming_phones and incoming_phones & _row_phone_keys(row):
                return row

    nm = name.strip().lower()
    cm = company_name.strip().lower()
    if not nm or not cm:
        return None
    stmt = select(Prospect).where(
        Prospect.campaign_id == campaign_id,
        func.lower(Prospect.name) == nm,
        func.lower(Prospect.company_name) == cm,
    )
    return session.scalars(stmt).first()


def find_duplicate_in_company(
    session: Session,
    *,
    company_id: int,
    linkedin_url: str | None,
    email: str | None,
    phone: str | None = None,
    whatsapp: str | None = None,
    exclude_prospect_id: int | None = None,
) -> Prospect | None:
    """
    Evita el mismo contacto en dos campañas / vendedores de la empresa
    (email, slug LinkedIn o teléfono usable).
    """
    em = (email or "").strip().lower()
    if em and "@" in em:
        stmt = select(Prospect).where(
            Prospect.company_id == company_id,
            func.lower(Prospect.email) == em,
        )
        if exclude_prospect_id is not None:
            stmt = stmt.where(Prospect.id != exclude_prospect_id)
        hit = session.scalars(stmt).first()
        if hit is not None:
            return hit

    slug = linkedin_slug_key(linkedin_url)
    incoming_phones = phone_identity_keys(phone, whatsapp)
    if not slug and not incoming_phones:
        return None

    rows = session.scalars(select(Prospect).where(Prospect.company_id == company_id)).all()
    for row in rows:
        if exclude_prospect_id is not None and row.id == exclude_prospect_id:
            continue
        if slug and linkedin_slug_key(row.linkedin_url) == slug:
            return row
        if incoming_phones and incoming_phones & _row_phone_keys(row):
            return row
    return None
