"""Upsert a la base propia Nexus (cache global) — nunca debe romper import/enrich."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.nexus_contact_cache import (
    NexusCompanyCache,
    NexusContactCache,
    NexusContactDelivery,
)
from app.models.prospect import Prospect
from app.services.lead_sourcing.linkedin_identity import (
    linkedin_profile_slug,
    normalize_linkedin_url,
)
from app.services.whatsapp_cloud_service import sanitize_stored_email, sanitize_stored_phone

_logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_domain(raw: str | None) -> str | None:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "://" in s:
        try:
            s = urlparse(s).netloc or s
        except Exception:  # noqa: BLE001
            pass
    s = s.removeprefix("www.").split("/")[0].strip()
    if not s or "." not in s:
        return None
    return s[:255]


def _phone_digits(raw: str | None) -> str | None:
    p = sanitize_stored_phone(raw)
    if not p:
        return None
    digits = re.sub(r"\D+", "", p)
    return digits if len(digits) >= 8 else None


def _fill_if_empty(row: Any, field: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    cur = getattr(row, field, None)
    if cur in (None, "", []):
        setattr(row, field, value)


def _upsert_company(
    db: Session,
    *,
    name: str | None,
    domain: str | None,
    website_url: str | None,
    industry: str | None,
    country: str | None,
    source_provider: str | None,
) -> NexusCompanyCache | None:
    dom = normalize_domain(domain) or normalize_domain(website_url)
    cname = (name or "").strip()
    if not dom and not cname:
        return None

    row: NexusCompanyCache | None = None
    if dom:
        row = db.scalars(
            select(NexusCompanyCache).where(NexusCompanyCache.domain == dom).limit(1)
        ).first()
    if row is None and cname:
        row = db.scalars(
            select(NexusCompanyCache)
            .where(NexusCompanyCache.name == cname)
            .limit(1)
        ).first()

    if row is None:
        row = NexusCompanyCache(
            name=cname or (dom or "—"),
            domain=dom,
            website_url=(website_url or "").strip() or None,
            industry=(industry or "").strip() or None,
            country=(country or "").strip() or None,
            source_provider=(source_provider or "").strip() or None,
            last_enriched_at=_now(),
        )
        db.add(row)
        db.flush()
        return row

    _fill_if_empty(row, "name", cname)
    _fill_if_empty(row, "domain", dom)
    _fill_if_empty(row, "website_url", (website_url or "").strip() or None)
    _fill_if_empty(row, "industry", (industry or "").strip() or None)
    _fill_if_empty(row, "country", (country or "").strip() or None)
    row.last_enriched_at = _now()
    db.flush()
    return row


def find_company_domain_by_name(
    db: Session, company_name: str
) -> tuple[str, str | None] | None:
    """Dominio ya conocido en base propia (por nombre de empleadora)."""
    cname = (company_name or "").strip()
    if not cname:
        return None
    row = db.scalars(
        select(NexusCompanyCache).where(NexusCompanyCache.name == cname).limit(1)
    ).first()
    if row is None:
        row = db.scalars(
            select(NexusCompanyCache)
            .where(func.lower(NexusCompanyCache.name) == cname.lower())
            .limit(1)
        ).first()
    if row is None or not (row.domain or "").strip():
        return None
    return ((row.domain or "").strip().lower(), row.website_url)


def remember_company_domain(
    db: Session,
    *,
    name: str,
    domain: str,
    website_url: str | None = None,
    industry: str | None = None,
    source_provider: str | None = "domain_resolver",
) -> None:
    """Persiste dominio resuelto para no repetir Brave/Prospeo."""
    _upsert_company(
        db,
        name=name,
        domain=domain,
        website_url=website_url,
        industry=industry,
        country=None,
        source_provider=source_provider,
    )


def find_company_industry_by_name(db: Session, company_name: str) -> str | None:
    hit = find_company_domain_by_name(db, company_name)
    if not hit:
        return None
    cname = (company_name or "").strip()
    if not cname:
        return None
    row = db.scalars(
        select(NexusCompanyCache).where(NexusCompanyCache.name == cname).limit(1)
    ).first()
    if row is None:
        row = db.scalars(
            select(NexusCompanyCache)
            .where(func.lower(NexusCompanyCache.name) == cname.lower())
            .limit(1)
        ).first()
    if row is None:
        return None
    ind = (row.industry or "").strip()
    return ind or None


def _find_contact(
    db: Session,
    *,
    email: str | None,
    linkedin_slug: str | None,
    phone_digits: str | None,
    source_provider: str | None,
    source_external_id: str | None,
) -> NexusContactCache | None:
    if email:
        hit = db.scalars(
            select(NexusContactCache).where(NexusContactCache.email == email).limit(1)
        ).first()
        if hit:
            return hit
    if linkedin_slug:
        hit = db.scalars(
            select(NexusContactCache)
            .where(NexusContactCache.linkedin_slug == linkedin_slug)
            .limit(1)
        ).first()
        if hit:
            return hit
    if phone_digits:
        hit = db.scalars(
            select(NexusContactCache).where(NexusContactCache.phone == phone_digits).limit(1)
        ).first()
        if hit:
            return hit
    sid = (source_external_id or "").strip()
    sp = (source_provider or "").strip()
    if sid and sp:
        hit = db.scalars(
            select(NexusContactCache)
            .where(
                NexusContactCache.source_provider == sp,
                NexusContactCache.source_external_id == sid,
            )
            .limit(1)
        ).first()
        if hit:
            return hit
    return None


def _record_delivery(
    db: Session,
    *,
    contact: NexusContactCache,
    tenant_company_id: int,
    campaign_id: int | None,
    prospect_id: int | None,
) -> None:
    existing = db.scalars(
        select(NexusContactDelivery).where(
            NexusContactDelivery.contact_cache_id == contact.id,
            NexusContactDelivery.tenant_company_id == int(tenant_company_id),
        ).limit(1)
    ).first()
    if existing:
        if prospect_id and not existing.prospect_id:
            existing.prospect_id = int(prospect_id)
        if campaign_id and not existing.campaign_id:
            existing.campaign_id = int(campaign_id)
        return
    db.add(
        NexusContactDelivery(
            contact_cache_id=contact.id,
            tenant_company_id=int(tenant_company_id),
            campaign_id=int(campaign_id) if campaign_id else None,
            prospect_id=int(prospect_id) if prospect_id else None,
            delivered_at=_now(),
        )
    )


def upsert_contact_from_import(
    db: Session,
    *,
    tenant_company_id: int,
    campaign_id: int | None,
    prospect: Prospect | None = None,
    name: str | None = None,
    role: str | None = None,
    industry: str | None = None,
    country: str | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
    phone: str | None = None,
    whatsapp: str | None = None,
    company_name: str | None = None,
    company_domain: str | None = None,
    company_website: str | None = None,
    source_provider: str | None = None,
    source_external_id: str | None = None,
) -> NexusContactCache | None:
    """Guarda/actualiza contacto+empresa. None si no hay ancla (email/LI/tel)."""
    if prospect is not None:
        name = name or prospect.name
        role = role or prospect.role
        industry = industry or prospect.industry
        country = country or prospect.country
        email = email or prospect.email
        linkedin_url = linkedin_url or prospect.linkedin_url
        phone = phone or prospect.phone
        whatsapp = whatsapp or prospect.whatsapp
        company_name = company_name or prospect.company_name
        company_website = company_website or prospect.company_website
        source_provider = source_provider or prospect.source_provider
        source_external_id = source_external_id or prospect.source_external_id
        campaign_id = campaign_id or prospect.campaign_id

    em = sanitize_stored_email(email)
    li = normalize_linkedin_url(linkedin_url)
    slug = linkedin_profile_slug(li) if li else None
    phone_d = _phone_digits(phone) or _phone_digits(whatsapp)
    wa = sanitize_stored_phone(whatsapp) or sanitize_stored_phone(phone)

    if not em and not slug and not phone_d:
        return None

    employer = _upsert_company(
        db,
        name=company_name,
        domain=company_domain,
        website_url=company_website,
        industry=industry,
        country=country,
        source_provider=source_provider,
    )

    row = _find_contact(
        db,
        email=em,
        linkedin_slug=slug,
        phone_digits=phone_d,
        source_provider=source_provider,
        source_external_id=source_external_id,
    )
    if row is None:
        row = NexusContactCache(
            company_cache_id=employer.id if employer else None,
            full_name=(name or "").strip() or "—",
            role=(role or "").strip() or None,
            industry=(industry or "").strip() or None,
            country=(country or "").strip() or None,
            email=em,
            linkedin_url=li,
            linkedin_slug=slug,
            phone=phone_d,
            whatsapp=wa,
            company_name=(company_name or "").strip() or None,
            company_domain=normalize_domain(company_domain)
            or normalize_domain(company_website),
            source_provider=(source_provider or "").strip() or None,
            source_external_id=(source_external_id or "").strip() or None,
            last_enriched_at=_now(),
        )
        db.add(row)
        db.flush()
    else:
        _fill_if_empty(row, "full_name", (name or "").strip() or None)
        _fill_if_empty(row, "role", (role or "").strip() or None)
        _fill_if_empty(row, "industry", (industry or "").strip() or None)
        _fill_if_empty(row, "country", (country or "").strip() or None)
        _fill_if_empty(row, "email", em)
        _fill_if_empty(row, "linkedin_url", li)
        _fill_if_empty(row, "linkedin_slug", slug)
        _fill_if_empty(row, "phone", phone_d)
        _fill_if_empty(row, "whatsapp", wa)
        _fill_if_empty(row, "company_name", (company_name or "").strip() or None)
        _fill_if_empty(
            row,
            "company_domain",
            normalize_domain(company_domain) or normalize_domain(company_website),
        )
        if employer and not row.company_cache_id:
            row.company_cache_id = employer.id
        # Preferir datos más ricos: si llega teléfono nuevo y no había, ya fill_if_empty.
        # Si llega teléfono y había vacío — ok. Si enrich trae móvil y teníamos vacío:
        if phone_d and not row.phone:
            row.phone = phone_d
        if wa and not row.whatsapp:
            row.whatsapp = wa
        if em and not row.email:
            row.email = em
        row.last_enriched_at = _now()
        db.flush()

    _record_delivery(
        db,
        contact=row,
        tenant_company_id=tenant_company_id,
        campaign_id=campaign_id,
        prospect_id=prospect.id if prospect is not None else None,
    )
    db.flush()
    return row


def safe_upsert_from_prospect(
    db: Session,
    prospect: Prospect,
    *,
    tenant_company_id: int | None = None,
) -> None:
    """Best-effort: nunca propaga excepción."""
    try:
        tid = int(tenant_company_id or prospect.company_id)
        upsert_contact_from_import(
            db,
            tenant_company_id=tid,
            campaign_id=prospect.campaign_id,
            prospect=prospect,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.info(
            "nexus cache upsert skipped prospect=%s: %s",
            getattr(prospect, "id", None),
            exc,
        )


def tenant_delivered_exclusion_sets(
    db: Session,
    tenant_company_id: int,
) -> tuple[set[str], set[str], set[str]]:
    """Emails / LI slugs / phones ya entregados a este cliente Nexus (anti-dupe)."""
    from app.services.prospect_ingestion import phone_identity_keys

    emails: set[str] = set()
    linkedin: set[str] = set()
    phones: set[str] = set()
    try:
        rows = db.execute(
            select(
                NexusContactCache.email,
                NexusContactCache.linkedin_slug,
                NexusContactCache.linkedin_url,
                NexusContactCache.phone,
                NexusContactCache.whatsapp,
            )
            .join(
                NexusContactDelivery,
                NexusContactDelivery.contact_cache_id == NexusContactCache.id,
            )
            .where(NexusContactDelivery.tenant_company_id == int(tenant_company_id))
        ).all()
    except Exception as exc:  # noqa: BLE001
        _logger.info("tenant delivered exclusion failed tenant=%s: %s", tenant_company_id, exc)
        return emails, linkedin, phones

    for em, slug, li_url, phone, wa in rows:
        if em:
            emails.add(str(em).strip().lower())
        s = (slug or "").strip().lower()
        if not s and li_url:
            from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key

            s = (linkedin_slug_key(li_url) or "").strip().lower()
        if s:
            linkedin.add(s)
        phones |= phone_identity_keys(phone, wa)
    return emails, linkedin, phones


def contact_delivered_to_tenant(
    db: Session,
    tenant_company_id: int,
    *,
    email: str | None = None,
    linkedin_url: str | None = None,
    phone: str | None = None,
    whatsapp: str | None = None,
) -> bool:
    """True si este cliente ya recibió ese contacto (misma identidad)."""
    em_set, li_set, ph_set = tenant_delivered_exclusion_sets(db, tenant_company_id)
    em = sanitize_stored_email(email)
    if em and em.lower() in em_set:
        return True
    from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key

    slug = (linkedin_slug_key(linkedin_url) or "").strip().lower()
    if slug and slug in li_set:
        return True
    from app.services.prospect_ingestion import phone_identity_keys

    if phone_identity_keys(phone, whatsapp) & ph_set:
        return True
    return False


def merge_exclusion_sets(
    *sets_triple: tuple[set[str], set[str], set[str]],
) -> tuple[set[str], set[str], set[str]]:
    emails: set[str] = set()
    linkedin: set[str] = set()
    phones: set[str] = set()
    for em, li, ph in sets_triple:
        emails |= em
        linkedin |= li
        phones |= ph
    return emails, linkedin, phones


def find_cached_leads_for_campaign(
    db: Session,
    campaign: Any,
    *,
    limit: int = 20,
    exclude_emails: set[str] | None = None,
    exclude_linkedin: set[str] | None = None,
    exclude_phones: set[str] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Devuelve leads del cache Nexus que matchean ICP y NO fueron entregados a este tenant."""
    return _find_cached_leads(
        db,
        campaign,
        limit=limit,
        exclude_emails=exclude_emails,
        exclude_linkedin=exclude_linkedin,
        exclude_phones=exclude_phones,
    )


def _normalize_company_match_key(name: str | None) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    return s[:180]


def _company_identity_match(
    *,
    row_domain: str | None,
    row_name: str | None,
    target_domain: str | None,
    target_name: str | None,
) -> bool:
    td = normalize_domain(target_domain) or ""
    rd = normalize_domain(row_domain) or ""
    if td and rd and td == rd:
        return True
    tn = _normalize_company_match_key(target_name)
    rn = _normalize_company_match_key(row_name)
    if tn and rn and len(tn) >= 3 and (tn == rn or tn in rn or rn in tn):
        return True
    return False


def _contact_passes_icp_and_exclusions(
    row: NexusContactCache,
    campaign: Any,
    *,
    excl_em: set[str],
    excl_li: set[str],
    excl_phones: set[str],
    diag: dict[str, Any],
) -> tuple[bool, int]:
    from app.services.campaign_icp import is_icp_token_empty
    from app.services.lead_sourcing.icp_import_gate import MIN_GEO_HARD, MIN_ROLE_MATCH_FOR_IMPORT, geo_hard_score
    from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url, linkedin_slug_key
    from app.services.prospect_ingestion import phone_identity_keys
    from app.services.lead_sourcing.role_alignment import best_icp_role_match

    em = (row.email or "").strip().lower() or None
    li = (row.linkedin_url or "").strip() or None
    slug = (row.linkedin_slug or "").strip().lower() or linkedin_slug_key(li) or ""
    if not em and not is_personal_linkedin_url(li):
        diag["skipped_no_channel"] = int(diag.get("skipped_no_channel") or 0) + 1
        return False, 0
    phone_keys = phone_identity_keys(row.phone, row.whatsapp)
    if (em and em in excl_em) or (slug and slug in excl_li) or (phone_keys & excl_phones):
        diag["skipped_exclude"] = int(diag.get("skipped_exclude") or 0) + 1
        return False, 0

    target_role = (getattr(campaign, "target_role", None) or "").strip()
    role = (row.role or "").strip()
    if target_role and not is_icp_token_empty(target_role):
        role_score, _ = best_icp_role_match(target_role, role)
        if role_score < MIN_ROLE_MATCH_FOR_IMPORT:
            diag["skipped_role"] = int(diag.get("skipped_role") or 0) + 1
            return False, 0
    else:
        role_score = 70

    target_country = (getattr(campaign, "target_country", None) or "").strip()
    country = (row.country or "").strip()
    if target_country and not is_icp_token_empty(target_country) and country:
        geo_score, _ = geo_hard_score(country, target_country)
        if geo_score < MIN_GEO_HARD:
            diag["skipped_geo"] = int(diag.get("skipped_geo") or 0) + 1
            return False, 0

    target_industry = (getattr(campaign, "target_industry", None) or "").strip()
    ind = (row.industry or "").strip()
    if (
        target_industry
        and not is_icp_token_empty(target_industry)
        and ind
        and target_industry.lower() not in ind.lower()
        and ind.lower() not in target_industry.lower()
    ):
        role_score = min(role_score, 65)

    return True, role_score


def _row_to_cached_lead(row: NexusContactCache, *, role_score: int) -> Any:
    from app.schemas.lead_sourcing import LeadCandidateRead

    score = max(55, min(92, int(role_score)))
    em = (row.email or "").strip().lower() or None
    li = (row.linkedin_url or "").strip() or None
    role = (row.role or "").strip()
    ind = (row.industry or "").strip()
    country = (row.country or "").strip()
    company_name = (row.company_name or "").strip() or "—"
    return LeadCandidateRead(
        external_id=f"nexus-cache-{row.id}",
        provider="nexus_cache",
        name=(row.full_name or "").strip() or "—",
        company_name=company_name,
        role=role or None,
        industry=ind or None,
        country=country or None,
        email=em,
        linkedin_url=li,
        phone=row.phone,
        whatsapp=row.whatsapp or row.phone,
        whatsapp_number=row.whatsapp or row.phone,
        company_domain=row.company_domain,
        company_website=(
            f"https://{row.company_domain}" if row.company_domain else None
        ),
        compatibility_score=score,
        fit_tier="good" if score >= 70 else "low_fit",
        has_email=bool(em),
        has_phone=bool(row.phone or row.whatsapp),
        has_linkedin=bool(li),
        enrichment_source="nexus_cache",
        enrichment_confidence=80,
        contact_kind="person",
        visible_in_panel=True,
        score_breakdown="nexus_cache",
    )


def _find_cached_leads(
    db: Session,
    campaign: Any,
    *,
    limit: int = 20,
    exclude_emails: set[str] | None = None,
    exclude_linkedin: set[str] | None = None,
    exclude_phones: set[str] | None = None,
    company_domain: str | None = None,
    company_name: str | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key

    diag: dict[str, Any] = {
        "source": "nexus_cache",
        "scanned": 0,
        "kept": 0,
        "skipped_delivered": 0,
        "skipped_exclude": 0,
        "skipped_role": 0,
        "skipped_geo": 0,
        "skipped_no_channel": 0,
        "skipped_company": 0,
    }
    limit = max(0, int(limit))
    if limit <= 0:
        return [], diag

    try:
        tenant_id = int(campaign.company_id)
    except Exception:  # noqa: BLE001
        return [], diag

    excl_em = {(e or "").strip().lower() for e in (exclude_emails or set()) if e}
    excl_li: set[str] = set()
    for u in exclude_linkedin or set():
        key = linkedin_slug_key(u) or str(u or "").strip().lower()
        if key:
            excl_li.add(key)
    excl_phones = {str(p).strip() for p in (exclude_phones or set()) if p}

    delivered_ids = select(NexusContactDelivery.contact_cache_id).where(
        NexusContactDelivery.tenant_company_id == tenant_id
    )
    q = (
        select(NexusContactCache)
        .where(NexusContactCache.id.notin_(delivered_ids))
        .order_by(NexusContactCache.id.desc())
        .limit(max(80, limit * 8))
    )
    try:
        rows = list(db.scalars(q).all())
    except Exception as exc:  # noqa: BLE001
        _logger.info(
            "nexus cache lookup failed campaign=%s: %s",
            getattr(campaign, "id", None),
            exc,
        )
        diag["error"] = str(exc)[:200]
        return [], diag

    leads: list[Any] = []
    for row in rows:
        diag["scanned"] += 1
        if company_domain or company_name:
            if not _company_identity_match(
                row_domain=row.company_domain,
                row_name=row.company_name,
                target_domain=company_domain,
                target_name=company_name,
            ):
                diag["skipped_company"] = int(diag.get("skipped_company") or 0) + 1
                continue
        ok, role_score = _contact_passes_icp_and_exclusions(
            row,
            campaign,
            excl_em=excl_em,
            excl_li=excl_li,
            excl_phones=excl_phones,
            diag=diag,
        )
        if not ok:
            continue
        leads.append(_row_to_cached_lead(row, role_score=role_score))
        if len(leads) >= limit:
            break

    diag["kept"] = len(leads)
    return leads, diag


def find_cached_contacts_for_company(
    db: Session,
    campaign: Any,
    *,
    company_domain: str | None,
    company_name: str | None,
    limit: int = 3,
    exclude_emails: set[str] | None = None,
    exclude_linkedin: set[str] | None = None,
    exclude_phones: set[str] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Contactos cacheados para una empresa concreta (company-first enrich)."""
    return _find_cached_leads(
        db,
        campaign,
        limit=limit,
        exclude_emails=exclude_emails,
        exclude_linkedin=exclude_linkedin,
        exclude_phones=exclude_phones,
        company_domain=company_domain,
        company_name=company_name,
    )


def find_cached_companies_for_campaign(
    db: Session,
    campaign: Any,
    *,
    limit: int = 20,
) -> tuple[list[Any], dict[str, Any]]:
    """Empresas deducidas del cache (con contactos reutilizables) — antes de Brave."""
    from app.schemas.lead_sourcing import CompanyCandidateRead
    from app.services.lead_sourcing.company_relevance import canonical_company_key

    diag: dict[str, Any] = {
        "source": "nexus_company_cache",
        "scanned": 0,
        "kept": 0,
        "skipped_no_company": 0,
    }
    limit = max(0, int(limit))
    if limit <= 0:
        return [], diag

    try:
        tenant_id = int(campaign.company_id)
    except Exception:  # noqa: BLE001
        return [], diag

    delivered_ids = select(NexusContactDelivery.contact_cache_id).where(
        NexusContactDelivery.tenant_company_id == tenant_id
    )
    rows = list(
        db.scalars(
            select(NexusContactCache)
            .where(NexusContactCache.id.notin_(delivered_ids))
            .order_by(NexusContactCache.id.desc())
            .limit(max(150, limit * 12))
        ).all()
    )

    grouped: dict[str, dict[str, Any]] = {}
    filter_diag = dict(diag)
    for row in rows:
        diag["scanned"] += 1
        ok, role_score = _contact_passes_icp_and_exclusions(
            row,
            campaign,
            excl_em=set(),
            excl_li=set(),
            excl_phones=set(),
            diag=filter_diag,
        )
        if not ok:
            continue
        dom = normalize_domain(row.company_domain)
        name = (row.company_name or "").strip()
        if not dom and not name:
            diag["skipped_no_company"] = int(diag.get("skipped_no_company") or 0) + 1
            continue
        key = dom or _normalize_company_match_key(name)
        if not key:
            continue
        bucket = grouped.get(key)
        if bucket is None:
            web = f"https://{dom}" if dom else None
            grouped[key] = {
                "domain": dom,
                "name": name or dom or "—",
                "industry": (row.industry or "").strip() or None,
                "country": (row.country or "").strip() or None,
                "website": web,
                "best_role_score": role_score,
                "contacts": 1,
            }
        else:
            bucket["contacts"] += 1
            bucket["best_role_score"] = max(int(bucket["best_role_score"]), role_score)
            if not bucket.get("industry") and row.industry:
                bucket["industry"] = (row.industry or "").strip() or None
            if not bucket.get("country") and row.country:
                bucket["country"] = (row.country or "").strip() or None

    companies: list[CompanyCandidateRead] = []
    for key, meta in sorted(
        grouped.items(),
        key=lambda kv: (-int(kv[1]["contacts"]), -int(kv[1]["best_role_score"])),
    ):
        if len(companies) >= limit:
            break
        dom = meta.get("domain")
        name = str(meta.get("name") or "—")
        web = meta.get("website")
        score = max(60, min(88, int(meta.get("best_role_score") or 70)))
        canon = canonical_company_key(web or (f"https://{dom}" if dom else ""), name)
        companies.append(
            CompanyCandidateRead(
                external_id=f"nexus-co-{key[:40]}",
                provider="nexus_cache",
                name=name[:255],
                website_url=web,
                industry=meta.get("industry"),
                country=meta.get("country"),
                company_domain=dom,
                domain_source="nexus_cache",
                domain_trust="verified" if dom else "unresolved",
                icp_relevance_score=score,
                confidence=score,
                canonical_key=canon,
                normalized_company_name=name[:255],
                result_kind="company",
                enrichment_source="nexus_cache",
                enrichment_confidence=80,
            )
        )

    diag["kept"] = len(companies)
    return companies, diag
