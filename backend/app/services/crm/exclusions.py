"""
Exclusión CRM: importa cuentas/contactos ya tocados en HubSpot/Salesforce
para que Nexus no los vuelva a prospectar.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.crm_exclusion import CrmExclusion
from app.services.crm import company_credentials as cc
from app.services.lead_sourcing.company_name_normalizer import normalize_company_name
from app.services.lead_sourcing.prospeo_contact_validation import (
    email_domain,
    is_consumer_email_domain,
)

_logger = logging.getLogger("nexus.crm.exclusions")

MATCH_EMAIL = "email"
MATCH_DOMAIN = "domain"
MATCH_COMPANY = "company_name"
PROVIDER_MANUAL = "manual"

# Límite defensivo para no colgar el OAuth callback.
_HUBSPOT_MAX_PAGES = 50
_HUBSPOT_PAGE_SIZE = 100
_SF_MAX_RECORDS = 5000


@dataclass(frozen=True)
class ExclusionHit:
    match_type: str
    match_value: str
    external_id: str | None = None
    label: str | None = None
    reason: str | None = None


@dataclass
class ExclusionSyncResult:
    provider: str
    ok: bool
    inserted: int = 0
    updated: int = 0
    total: int = 0
    scanned: int = 0
    error: str | None = None


def normalize_domain(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "://" in raw or "/" in raw:
        try:
            if "://" not in raw:
                raw = f"https://{raw}"
            host = urlparse(raw).netloc or urlparse(raw).path
            raw = host
        except Exception:
            pass
    raw = raw.split("/")[0].split("?")[0].strip().removeprefix("www.")
    if not raw or "." not in raw:
        return None
    if is_consumer_email_domain(raw):
        return None
    return raw


def normalize_email(value: str | None) -> str | None:
    em = (value or "").strip().lower()
    if not em or "@" not in em:
        return None
    return em


def normalize_company_key(value: str | None) -> str | None:
    cleaned = normalize_company_name(value or "") or (value or "").strip()
    if not cleaned:
        return None
    key = re.sub(r"[^\w\s]", " ", cleaned.lower())
    key = re.sub(r"\s+", " ", key).strip()
    return key or None


def _website_domain(url: str | None) -> str | None:
    return normalize_domain(url)


def is_crm_excluded(
    db: Session,
    company_id: int,
    *,
    email: str | None = None,
    company_name: str | None = None,
    company_website: str | None = None,
    company_domain: str | None = None,
) -> CrmExclusion | None:
    """Devuelve la primera exclusión que matchee email, dominio o nombre de empresa."""
    checks: list[tuple[str, str]] = []
    em = normalize_email(email)
    if em:
        checks.append((MATCH_EMAIL, em))
        dom = email_domain(em)
        if dom and not is_consumer_email_domain(dom):
            checks.append((MATCH_DOMAIN, dom))
    for candidate in (company_domain, company_website):
        dom = normalize_domain(candidate)
        if dom:
            checks.append((MATCH_DOMAIN, dom))
    cname = normalize_company_key(company_name)
    if cname:
        checks.append((MATCH_COMPANY, cname))

    seen: set[tuple[str, str]] = set()
    for match_type, match_value in checks:
        key = (match_type, match_value)
        if key in seen:
            continue
        seen.add(key)
        hit = db.scalars(
            select(CrmExclusion).where(
                CrmExclusion.company_id == company_id,
                CrmExclusion.match_type == match_type,
                CrmExclusion.match_value == match_value,
            )
        ).first()
        if hit is not None:
            return hit
    return None


def exclusion_status(db: Session, company_id: int) -> dict[str, Any]:
    rows = db.scalars(
        select(CrmExclusion).where(CrmExclusion.company_id == company_id)
    ).all()
    by_provider: dict[str, int] = {}
    by_type: dict[str, int] = {}
    last_synced_at = None
    for row in rows:
        by_provider[row.provider] = by_provider.get(row.provider, 0) + 1
        by_type[row.match_type] = by_type.get(row.match_type, 0) + 1
        ts = row.updated_at or row.created_at
        if ts and (last_synced_at is None or ts > last_synced_at):
            last_synced_at = ts
    return {
        "total": len(rows),
        "by_provider": by_provider,
        "by_type": by_type,
        "hubspot_active": cc.hubspot_active(db, company_id),
        "salesforce_active": cc.salesforce_active(db, company_id),
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "auto_sync": True,
    }


def companies_with_crm(db: Session) -> list[int]:
    """Empresas con HubSpot o Salesforce conectado (OAuth en BD)."""
    from app.models.company_integration import CompanyIntegration
    from app.models.enums import IntegrationStatus

    rows = db.scalars(
        select(CompanyIntegration.company_id).where(
            CompanyIntegration.provider.in_(
                [cc.PROVIDER_HUBSPOT, cc.PROVIDER_SALESFORCE]
            ),
            CompanyIntegration.status == IntegrationStatus.connected.value,
        )
    ).all()
    return sorted({int(cid) for cid in rows if cid})


def sync_exclusions_all_companies(db: Session) -> dict[str, Any]:
    """Pull automático de exclusiones desde CRM para todas las empresas conectadas."""
    company_ids = companies_with_crm(db)
    results: list[dict[str, Any]] = []
    for company_id in company_ids:
        try:
            sync_results = sync_exclusions_for_company(db, company_id)
            db.commit()
            results.append(
                {
                    "company_id": company_id,
                    "ok": all(r.ok for r in sync_results) if sync_results else True,
                    "providers": [
                        {
                            "provider": r.provider,
                            "ok": r.ok,
                            "total": r.total,
                            "inserted": r.inserted,
                            "error": r.error,
                        }
                        for r in sync_results
                    ],
                }
            )
        except Exception as exc:
            db.rollback()
            _logger.warning("CRM exclusion auto-sync company=%s: %s", company_id, exc)
            results.append({"company_id": company_id, "ok": False, "error": str(exc)[:300]})
    return {"companies": len(company_ids), "results": results}


def clear_manual_exclusions(db: Session, company_id: int) -> int:
    """Borra solo exclusiones cargadas a mano (no toca HubSpot/Salesforce)."""
    rows = db.scalars(
        select(CrmExclusion).where(
            CrmExclusion.company_id == company_id,
            CrmExclusion.provider == PROVIDER_MANUAL,
        )
    ).all()
    n = len(rows)
    if n:
        db.execute(
            delete(CrmExclusion).where(
                CrmExclusion.company_id == company_id,
                CrmExclusion.provider == PROVIDER_MANUAL,
            )
        )
        db.flush()
    return n


def _upsert_hits(
    db: Session,
    company_id: int,
    provider: str,
    hits: list[ExclusionHit],
) -> ExclusionSyncResult:
    """Reemplaza exclusiones del provider con el set nuevo (idempotente)."""
    unique: dict[tuple[str, str], ExclusionHit] = {}
    for hit in hits:
        if not hit.match_type or not hit.match_value:
            continue
        unique[(hit.match_type, hit.match_value)] = hit

    existing = db.scalars(
        select(CrmExclusion).where(
            CrmExclusion.company_id == company_id,
            CrmExclusion.provider == provider,
        )
    ).all()
    by_key = {(r.match_type, r.match_value): r for r in existing}

    inserted = 0
    updated = 0
    keep_keys: set[tuple[str, str]] = set()
    for key, hit in unique.items():
        keep_keys.add(key)
        row = by_key.get(key)
        if row is None:
            db.add(
                CrmExclusion(
                    company_id=company_id,
                    provider=provider,
                    match_type=hit.match_type,
                    match_value=hit.match_value,
                    external_id=(hit.external_id or None),
                    label=(hit.label[:255] if hit.label else None),
                    reason=(hit.reason[:2000] if hit.reason else None),
                )
            )
            inserted += 1
        else:
            changed = False
            if hit.external_id and row.external_id != hit.external_id:
                row.external_id = hit.external_id
                changed = True
            if hit.label and row.label != hit.label[:255]:
                row.label = hit.label[:255]
                changed = True
            if hit.reason and row.reason != hit.reason[:2000]:
                row.reason = hit.reason[:2000]
                changed = True
            if changed:
                updated += 1

    stale_ids = [r.id for k, r in by_key.items() if k not in keep_keys]
    if stale_ids:
        db.execute(delete(CrmExclusion).where(CrmExclusion.id.in_(stale_ids)))

    db.flush()
    return ExclusionSyncResult(
        provider=provider,
        ok=True,
        inserted=inserted,
        updated=updated,
        total=len(unique),
        scanned=len(hits),
    )


def merge_hits(
    db: Session,
    company_id: int,
    provider: str,
    hits: list[ExclusionHit],
) -> ExclusionSyncResult:
    """Agrega exclusiones sin borrar las existentes del mismo provider."""
    unique: dict[tuple[str, str], ExclusionHit] = {}
    for hit in hits:
        if not hit.match_type or not hit.match_value:
            continue
        unique[(hit.match_type, hit.match_value)] = hit

    existing = db.scalars(
        select(CrmExclusion).where(
            CrmExclusion.company_id == company_id,
            CrmExclusion.provider == provider,
        )
    ).all()
    by_key = {(r.match_type, r.match_value): r for r in existing}

    inserted = 0
    updated = 0
    for key, hit in unique.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                CrmExclusion(
                    company_id=company_id,
                    provider=provider,
                    match_type=hit.match_type,
                    match_value=hit.match_value,
                    external_id=(hit.external_id or None),
                    label=(hit.label[:255] if hit.label else None),
                    reason=(hit.reason[:2000] if hit.reason else None),
                )
            )
            inserted += 1
        else:
            changed = False
            if hit.label and row.label != hit.label[:255]:
                row.label = hit.label[:255]
                changed = True
            if hit.reason and row.reason != hit.reason[:2000]:
                row.reason = hit.reason[:2000]
                changed = True
            if changed:
                updated += 1

    db.flush()
    return ExclusionSyncResult(
        provider=provider,
        ok=True,
        inserted=inserted,
        updated=updated,
        total=len(unique),
        scanned=len(hits),
    )


def _hit_from_manual_token(raw: str) -> ExclusionHit | None:
    token = (raw or "").strip()
    if not token or token.startswith("#"):
        return None
    if "@" in token:
        em = normalize_email(token)
        if not em:
            return None
        return ExclusionHit(
            MATCH_EMAIL,
            em,
            label=em,
            reason="Manual exclusion",
        )
    dom = normalize_domain(token)
    if dom:
        return ExclusionHit(
            MATCH_DOMAIN,
            dom,
            label=dom,
            reason="Manual exclusion",
        )
    cname = normalize_company_key(token)
    if not cname:
        return None
    return ExclusionHit(
        MATCH_COMPANY,
        cname,
        label=token[:255],
        reason="Manual exclusion",
    )


def import_manual_exclusions_text(db: Session, company_id: int, raw_text: str) -> ExclusionSyncResult:
    """
    Importa exclusiones manuales (CSV o lista).
    Acepta:
    - una columna por línea (email / dominio / empresa)
    - CSV con encabezados email,domain,company (o company_name)
    """
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ExclusionSyncResult(provider=PROVIDER_MANUAL, ok=False, error="Texto vacío")

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    hits: list[ExclusionHit] = []

    if lines and ("," in lines[0] or ";" in lines[0]):
        sep = ";" if lines[0].count(";") > lines[0].count(",") else ","
        header = [h.strip().lower() for h in lines[0].split(sep)]
        known = {"email", "domain", "company", "company_name", "empresa", "dominio"}
        if any(h in known for h in header):
            idx = {h: i for i, h in enumerate(header)}
            for line in lines[1:]:
                cols = [c.strip() for c in line.split(sep)]
                for key, match_type, normalizer in (
                    ("email", MATCH_EMAIL, normalize_email),
                    ("domain", MATCH_DOMAIN, normalize_domain),
                    ("dominio", MATCH_DOMAIN, normalize_domain),
                    ("company", MATCH_COMPANY, normalize_company_key),
                    ("company_name", MATCH_COMPANY, normalize_company_key),
                    ("empresa", MATCH_COMPANY, normalize_company_key),
                ):
                    i = idx.get(key)
                    if i is None or i >= len(cols):
                        continue
                    val = normalizer(cols[i])
                    if not val:
                        continue
                    hits.append(
                        ExclusionHit(
                            match_type,
                            val,
                            label=cols[i][:255],
                            reason="Manual CSV exclusion",
                        )
                    )
            return merge_hits(db, company_id, PROVIDER_MANUAL, hits)

    for line in lines:
        # CSV simple sin header: tomar primera celda o toda la línea
        cell = line.split(",")[0].split(";")[0].strip()
        hit = _hit_from_manual_token(cell)
        if hit:
            hits.append(hit)

    if not hits:
        return ExclusionSyncResult(
            provider=PROVIDER_MANUAL,
            ok=False,
            error="No se detectaron emails, dominios o empresas válidas",
        )
    return merge_hits(db, company_id, PROVIDER_MANUAL, hits)


def _contact_was_touched_hubspot(props: dict[str, Any]) -> bool:
    if props.get("notes_last_contacted"):
        return True
    if props.get("hs_last_sales_activity_timestamp"):
        return True
    try:
        if int(props.get("num_contacted_notes") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    # Contactos con dueño comercial suelen ser trabajados aunque falte timestamp.
    if props.get("hubspot_owner_id"):
        return True
    return False


def _hits_from_hubspot_contact(props: dict[str, Any], external_id: str | None) -> list[ExclusionHit]:
    if not _contact_was_touched_hubspot(props):
        return []
    hits: list[ExclusionHit] = []
    reason = "HubSpot contact with prior activity"
    em = normalize_email(props.get("email"))
    if em:
        hits.append(
            ExclusionHit(
                MATCH_EMAIL,
                em,
                external_id=external_id,
                label=em,
                reason=reason,
            )
        )
    for raw_dom in (
        props.get("hs_email_domain"),
        email_domain(em) if em else None,
        props.get("website"),
    ):
        dom = normalize_domain(str(raw_dom) if raw_dom else None)
        if dom:
            hits.append(
                ExclusionHit(
                    MATCH_DOMAIN,
                    dom,
                    external_id=external_id,
                    label=dom,
                    reason=reason,
                )
            )
    cname = normalize_company_key(props.get("company"))
    if cname:
        hits.append(
            ExclusionHit(
                MATCH_COMPANY,
                cname,
                external_id=external_id,
                label=props.get("company"),
                reason=reason,
            )
        )
    return hits


def fetch_hubspot_touched_hits(*, access_token: str) -> list[ExclusionHit]:
    props = [
        "email",
        "company",
        "website",
        "hs_email_domain",
        "notes_last_contacted",
        "num_contacted_notes",
        "hs_last_sales_activity_timestamp",
        "hubspot_owner_id",
    ]
    hits: list[ExclusionHit] = []
    after: str | None = None
    with httpx.Client(timeout=40.0) as client:
        for _ in range(_HUBSPOT_MAX_PAGES):
            body: dict[str, Any] = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "notes_last_contacted",
                                "operator": "HAS_PROPERTY",
                            }
                        ]
                    },
                    {
                        "filters": [
                            {
                                "propertyName": "hs_last_sales_activity_timestamp",
                                "operator": "HAS_PROPERTY",
                            }
                        ]
                    },
                    {
                        "filters": [
                            {
                                "propertyName": "num_contacted_notes",
                                "operator": "GT",
                                "value": "0",
                            }
                        ]
                    },
                    {
                        "filters": [
                            {
                                "propertyName": "hubspot_owner_id",
                                "operator": "HAS_PROPERTY",
                            }
                        ]
                    },
                ],
                "properties": props,
                "limit": _HUBSPOT_PAGE_SIZE,
            }
            if after:
                body["after"] = after
            resp = client.post(
                "https://api.hubapi.com/crm/v3/objects/contacts/search",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HubSpot search {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            for row in data.get("results") or []:
                cid = str(row.get("id") or "") or None
                hits.extend(_hits_from_hubspot_contact(row.get("properties") or {}, cid))
            paging = data.get("paging") or {}
            next_page = (paging.get("next") or {}).get("after")
            if not next_page:
                break
            after = str(next_page)
    return hits


def fetch_salesforce_touched_hits(*, access_token: str, instance_url: str) -> list[ExclusionHit]:
    from app.services.crm.salesforce import _api_root, _auth_headers

    hits: list[ExclusionHit] = []
    root = _api_root(instance_url)
    headers = _auth_headers(access_token)

    contact_soql = (
        "SELECT Id, Email, Account.Name, Account.Website, "
        "LastActivityDate, Account.LastActivityDate "
        "FROM Contact "
        "WHERE LastActivityDate != null OR Account.LastActivityDate != null "
        f"LIMIT {_SF_MAX_RECORDS}"
    )
    account_soql = (
        "SELECT Id, Name, Website, LastActivityDate "
        "FROM Account "
        "WHERE LastActivityDate != null "
        f"LIMIT {_SF_MAX_RECORDS}"
    )

    with httpx.Client(timeout=40.0) as client:
        for soql, kind in ((contact_soql, "contact"), (account_soql, "account")):
            resp = client.get(
                f"{root}/query?q={quote(soql, safe='')}",
                headers=headers,
            )
            if resp.status_code != 200:
                # Orgs sin LastActivityDate en Account/Contact: no abortar sync completo.
                _logger.warning(
                    "Salesforce %s query %s: %s",
                    kind,
                    resp.status_code,
                    resp.text[:300],
                )
                continue
            records = resp.json().get("records") or []
            for rec in records:
                ext = str(rec.get("Id") or "") or None
                reason = f"Salesforce {kind} with LastActivityDate"
                if kind == "contact":
                    em = normalize_email(rec.get("Email"))
                    if em:
                        hits.append(
                            ExclusionHit(
                                MATCH_EMAIL,
                                em,
                                external_id=ext,
                                label=em,
                                reason=reason,
                            )
                        )
                        dom = normalize_domain(email_domain(em))
                        if dom:
                            hits.append(
                                ExclusionHit(
                                    MATCH_DOMAIN,
                                    dom,
                                    external_id=ext,
                                    label=dom,
                                    reason=reason,
                                )
                            )
                    account = rec.get("Account") or {}
                    if isinstance(account, dict):
                        cname = normalize_company_key(account.get("Name"))
                        if cname:
                            hits.append(
                                ExclusionHit(
                                    MATCH_COMPANY,
                                    cname,
                                    external_id=ext,
                                    label=account.get("Name"),
                                    reason=reason,
                                )
                            )
                        dom = _website_domain(account.get("Website"))
                        if dom:
                            hits.append(
                                ExclusionHit(
                                    MATCH_DOMAIN,
                                    dom,
                                    external_id=ext,
                                    label=dom,
                                    reason=reason,
                                )
                            )
                else:
                    cname = normalize_company_key(rec.get("Name"))
                    if cname:
                        hits.append(
                            ExclusionHit(
                                MATCH_COMPANY,
                                cname,
                                external_id=ext,
                                label=rec.get("Name"),
                                reason=reason,
                            )
                        )
                    dom = _website_domain(rec.get("Website"))
                    if dom:
                        hits.append(
                            ExclusionHit(
                                MATCH_DOMAIN,
                                dom,
                                external_id=ext,
                                label=dom,
                                reason=reason,
                            )
                        )
    return hits


def sync_hubspot_exclusions(db: Session, company_id: int) -> ExclusionSyncResult:
    if not cc.hubspot_active(db, company_id):
        return ExclusionSyncResult(
            provider=cc.PROVIDER_HUBSPOT,
            ok=False,
            error="HubSpot no activo para esta empresa",
        )
    token = cc.get_hubspot_access_token(db, company_id)
    if not token:
        return ExclusionSyncResult(
            provider=cc.PROVIDER_HUBSPOT,
            ok=False,
            error="Sin token HubSpot",
        )
    try:
        hits = fetch_hubspot_touched_hits(access_token=token)
        result = _upsert_hits(db, company_id, cc.PROVIDER_HUBSPOT, hits)
        result.scanned = len(hits)
        return result
    except Exception as exc:
        _logger.warning("HubSpot exclusion sync company=%s: %s", company_id, exc)
        return ExclusionSyncResult(
            provider=cc.PROVIDER_HUBSPOT,
            ok=False,
            error=str(exc)[:400],
        )


def sync_salesforce_exclusions(db: Session, company_id: int) -> ExclusionSyncResult:
    if not cc.salesforce_active(db, company_id):
        return ExclusionSyncResult(
            provider=cc.PROVIDER_SALESFORCE,
            ok=False,
            error="Salesforce no activo para esta empresa",
        )
    auth = cc.get_salesforce_auth(db, company_id)
    if not auth:
        return ExclusionSyncResult(
            provider=cc.PROVIDER_SALESFORCE,
            ok=False,
            error="Sin auth Salesforce",
        )
    access, instance_url = auth
    try:
        hits = fetch_salesforce_touched_hits(
            access_token=access,
            instance_url=instance_url,
        )
        result = _upsert_hits(db, company_id, cc.PROVIDER_SALESFORCE, hits)
        result.scanned = len(hits)
        return result
    except Exception as exc:
        _logger.warning("Salesforce exclusion sync company=%s: %s", company_id, exc)
        return ExclusionSyncResult(
            provider=cc.PROVIDER_SALESFORCE,
            ok=False,
            error=str(exc)[:400],
        )


def sync_exclusions_for_company(
    db: Session,
    company_id: int,
    *,
    providers: list[str] | None = None,
) -> list[ExclusionSyncResult]:
    wanted = providers or [cc.PROVIDER_HUBSPOT, cc.PROVIDER_SALESFORCE]
    results: list[ExclusionSyncResult] = []
    for provider in wanted:
        if provider == cc.PROVIDER_HUBSPOT:
            results.append(sync_hubspot_exclusions(db, company_id))
        elif provider == cc.PROVIDER_SALESFORCE:
            results.append(sync_salesforce_exclusions(db, company_id))
    return results


def sync_exclusions_best_effort(
    db: Session,
    company_id: int,
    *,
    provider: str | None = None,
) -> None:
    """Para OAuth callback: nunca rompe el flujo de conexión."""
    try:
        providers = [provider] if provider else None
        results = sync_exclusions_for_company(db, company_id, providers=providers)
        db.commit()
        for r in results:
            _logger.info(
                "CRM exclusion sync company=%s provider=%s ok=%s total=%s err=%s",
                company_id,
                r.provider,
                r.ok,
                r.total,
                r.error,
            )
    except Exception as exc:
        db.rollback()
        _logger.warning(
            "CRM exclusion sync best-effort failed company=%s: %s",
            company_id,
            exc,
        )
