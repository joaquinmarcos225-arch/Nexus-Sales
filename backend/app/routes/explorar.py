"""Explorar: buscador de personas (Prospeo) con revelado y alta a campaña."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.deps import get_company
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.models.user import User
from app.schemas.prospect import ProspectCreate
from app.services import explorar as svc
from app.services.nexus_contact_cache import safe_upsert_from_prospect

router = APIRouter(tags=["explorar"])


class ExplorarSearchRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    location: str | None = None
    industries: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    lookalike_linkedin_urls: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    headcounts: list[str] = Field(default_factory=list)
    # Default ON: Search prioriza cuentas con mail + celular (sin revelar valores).
    require_email: bool = True
    require_mobile: bool = True
    page: int = Field(default=1, ge=1, le=1000)


class ExplorarPerson(BaseModel):
    person_id: str = Field(min_length=1, max_length=64)
    source_person_id: str | None = Field(default=None, max_length=128)
    source_provider: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=255)
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=255)
    company_name: str | None = Field(default=None, max_length=255)
    company_domain: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=128)
    linkedin_url: str | None = Field(default=None, max_length=512)


class ExplorarFavoriteIn(ExplorarPerson):
    city: str | None = Field(default=None, max_length=128)
    company_size: str | None = Field(default=None, max_length=64)
    has_email: bool = False
    has_mobile: bool = False
    email_verified: bool = False
    mobile_verified: bool = False


class ExplorarAddRequest(BaseModel):
    campaign_id: int = Field(ge=1)
    people: list[ExplorarPerson] = Field(min_length=1, max_length=25)


def _raise(e: svc.ExplorarError) -> None:
    raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/companies/{company_id}/explorar/filter-catalog")
def explorar_filter_catalog(
    company_id: int,
    _company=Depends(get_company),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Chips de Explorar: solo valores que Prospeo acepta (fuente única)."""
    from app.services.explorar_filter_catalog import filter_catalog

    return filter_catalog()


@router.get("/companies/{company_id}/explorar/usage")
def explorar_usage(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    out = svc.usage_summary(db, company_id, user.id)
    db.commit()
    return out


@router.post("/companies/{company_id}/explorar/search")
def explorar_search(
    company_id: int,
    payload: ExplorarSearchRequest,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    from app.services.explorar_lookalike import resolve_lookalike_seeds

    seeds: list[dict[str, Any]] = []
    unresolved: list[str] = []
    try:
        if payload.lookalike_linkedin_urls:
            if not [t for t in payload.titles if str(t or "").strip()]:
                raise svc.ExplorarError("Elegí el cargo de a quién querés escribirle.")
            resolved = resolve_lookalike_seeds(db, payload.lookalike_linkedin_urls)
            if resolved.person_urls:
                raise svc.ExplorarError(
                    "Usá el link de la página de la empresa (linkedin.com/company/...), no el de una persona."
                )
            if resolved.invalid and not resolved.seeds and not resolved.unresolved:
                raise svc.ExplorarError(
                    "Pegá el link de la página de empresa en LinkedIn (linkedin.com/company/...)."
                )
            if not resolved.seeds:
                raise svc.ExplorarError(
                    "No encontramos esa empresa. Revisá que el link sea de la página de empresa en LinkedIn."
                )
            seeds = resolved.seeds
            unresolved = resolved.unresolved

        location = (payload.location or "").strip() or None
        if not location:
            from app.services.explorar_potential_customers import DEFAULT_SEARCH_LOCATION

            named = [n for n in payload.names if str(n or "").strip()]
            targeted = bool(
                payload.titles
                or payload.industries
                or payload.lookalike_linkedin_urls
                or payload.companies
            )
            if targeted and not named:
                location = DEFAULT_SEARCH_LOCATION
        filters = svc.build_filters(
            names=payload.names,
            titles=payload.titles,
            location=location,
            industries=payload.industries,
            seniorities=payload.seniorities,
            companies=payload.companies,
            departments=payload.departments,
            headcounts=payload.headcounts,
            lookalike_company_oids=[s["company_id"] for s in seeds],
            require_email=payload.require_email,
            require_mobile=payload.require_mobile,
        )
        out = svc.search(db, company_id=company_id, user_id=user.id, filters=filters, page=payload.page)
    except svc.ExplorarError as e:
        db.commit()
        _raise(e)
    db.commit()
    if seeds:
        out["lookalike_seeds"] = [
            {"name": s.get("name"), "linkedin_url": s.get("linkedin_url"), "domain": s.get("domain")}
            for s in seeds
        ]
        if unresolved:
            out["lookalike_unresolved"] = unresolved
    return out


class PotentialCustomersRequest(BaseModel):
    product_id: int = Field(ge=1)


@router.post("/companies/{company_id}/explorar/potential-customers")
def explorar_potential_customers(
    company_id: int,
    payload: PotentialCustomersRequest,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    from app.models.product import Product
    from app.services.explorar_potential_customers import potential_customer_filters

    product = db.get(Product, payload.product_id)
    if product is None or int(product.company_id) != int(company_id) or not product.is_active:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return potential_customer_filters(db, product)


@router.post("/companies/{company_id}/explorar/reveal")
def explorar_reveal(
    company_id: int,
    payload: ExplorarPerson,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        row, charged = svc.reveal_person(
            db, company_id=company_id, user_id=user.id, snapshot=payload.model_dump()
        )
    except svc.ExplorarError as e:
        db.rollback()
        _raise(e)
    db.commit()
    return {
        "person": svc.reveal_to_dict(row),
        "charged_credit": charged,
        "usage": svc.usage_summary(db, company_id, user.id),
    }


@router.get("/companies/{company_id}/explorar/favorites")
def explorar_favorites(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    from app.services.campaign_visibility import user_can_view_team_campaigns

    team = user_can_view_team_campaigns(user)
    return {
        "results": svc.list_favorites(db, company_id=company_id, user_id=user.id, include_team=team),
        "includes_team": team,
    }


@router.post("/companies/{company_id}/explorar/favorites")
def explorar_add_favorite(
    company_id: int,
    payload: ExplorarFavoriteIn,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        svc.add_favorite(db, company_id=company_id, user_id=user.id, snapshot=payload.model_dump())
    except svc.ExplorarError as e:
        db.rollback()
        _raise(e)
    try:
        with db.begin_nested():
            svc.store_in_contact_cache(db, company_id=company_id, hits=[payload.model_dump()], record_delivery=False)
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return {"ok": True, "person_id": payload.person_id}


@router.delete("/companies/{company_id}/explorar/favorites/{person_id}")
def explorar_remove_favorite(
    company_id: int,
    person_id: str,
    owner_user_id: int | None = None,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    from app.services.campaign_visibility import user_can_view_team_campaigns

    target = user.id
    if owner_user_id and int(owner_user_id) != int(user.id):
        if not user_can_view_team_campaigns(user):
            raise HTTPException(status_code=403, detail="Solo podés sacar tus propios guardados.")
        target = int(owner_user_id)
    removed = svc.remove_favorite(db, company_id=company_id, person_id=person_id, user_id=target)
    db.commit()
    return {"ok": True, "removed": removed}


def _drop_unused_reveal(db: Session, reveal) -> None:
    """Si el prospecto no entró a la campaña, el revelado sin cobro no queda disponible."""
    if reveal is None or getattr(reveal, "charged", False) or getattr(reveal, "prospect_id", None):
        return
    db.delete(reveal)
    db.flush()


@router.post("/companies/{company_id}/explorar/add-to-campaign")
def explorar_add_to_campaign(
    company_id: int,
    payload: ExplorarAddRequest,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    from app.routes.prospects import _persist_new_prospect
    from app.services.campaign_visibility import assert_campaign_access

    campaign = db.get(Campaign, payload.campaign_id)
    if campaign is None or int(campaign.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    assert_campaign_access(user, campaign)
    from app.services.prospect_in_use import blocking_message, build_in_use_index

    in_use = build_in_use_index(db, company_id, exclude_campaign_id=campaign.id)

    def _blocked(snap: dict[str, Any], reveal: Any, existing: Prospect | None) -> str | None:
        if existing is not None and int(existing.campaign_id) == int(campaign.id):
            return "Ya está en esta campaña."
        block = in_use.blocking(
            {
                **snap,
                "email": reveal.email if reveal is not None else None,
                "phone": reveal.phone if reveal is not None else None,
            }
        )
        return blocking_message(block) if block is not None else None

    results: list[dict[str, Any]] = []
    for person in payload.people:
        snap = person.model_dump()
        label = snap.get("name") or snap["person_id"]
        reveal = svc.get_reveal(db, company_id, snap["person_id"])
        if reveal is not None and reveal.prospect_id and reveal.campaign_id == campaign.id:
            existing_row = db.get(Prospect, reveal.prospect_id)
            if existing_row is not None and int(existing_row.campaign_id) == int(campaign.id):
                results.append({"person_id": snap["person_id"], "name": label, "ok": True, "already": True})
                continue

        existing = db.get(Prospect, reveal.prospect_id) if reveal is not None and reveal.prospect_id else None
        if existing is None:
            existing = svc.find_used_prospect(
                db,
                company_id=company_id,
                person_id=snap["person_id"],
                linkedin_url=(reveal.linkedin_url if reveal else None) or snap.get("linkedin_url"),
                email=reveal.email if reveal else None,
                phone=reveal.phone if reveal else None,
            )
        dup_error = _blocked(snap, reveal, existing)
        if dup_error:
            results.append({"person_id": snap["person_id"], "name": label, "ok": False, "error": dup_error})
            continue

        # Misma barra ICP que el sourcing automático (no meter pharma/eventos en SaaS).
        from app.services.lead_sourcing.icp_import_gate import icp_import_gate_reason

        gate_snap = reveal if reveal is not None else None
        gate_reason = icp_import_gate_reason(
            campaign_role=campaign.target_role,
            campaign_industry=campaign.target_industry,
            campaign_country=campaign.target_country,
            campaign_company_size=campaign.target_company_size,
            prospect_role=(gate_snap.role if gate_snap else None) or snap.get("role"),
            prospect_industry=(gate_snap.industry if gate_snap else None) or snap.get("industry"),
            prospect_country=(gate_snap.country if gate_snap else None) or snap.get("country"),
            company_name=(gate_snap.company_name if gate_snap else None) or snap.get("company_name"),
            company_domain=(gate_snap.company_domain if gate_snap else None)
            or snap.get("company_domain"),
            linkedin_url=(gate_snap.linkedin_url if gate_snap else None) or snap.get("linkedin_url"),
            email=gate_snap.email if gate_snap else None,
            industry_origin=snap.get("industry_origin") or "prospeo",
            fit_threshold=70,
        )
        if gate_reason:
            results.append(
                {
                    "person_id": snap["person_id"],
                    "name": label,
                    "ok": False,
                    "error": f"No encaja con el ICP de la campaña: {gate_reason}",
                }
            )
            continue

        # El crédito del revelado cubre una entrada a campaña (aunque ya esté en la base / individual).
        already_paid = reveal is not None and bool(reveal.charged)
        # Revelado sin cobro que solo vale si el prospecto entra a la campaña.
        fresh_reveal = None
        if reveal is None:
            try:
                reveal, _ = svc.reveal_person(
                    db,
                    company_id=company_id,
                    user_id=user.id,
                    snapshot=snap,
                    charge=False,
                    ensure_base=False,
                )
            except svc.ExplorarError as e:
                if not snap.get("linkedin_url"):
                    results.append({"person_id": snap["person_id"], "name": label, "ok": False, "error": str(e)})
                    continue
                reveal = None
            fresh_reveal = reveal
            if reveal is not None:
                existing = svc.find_used_prospect(
                    db,
                    company_id=company_id,
                    person_id=snap["person_id"],
                    linkedin_url=reveal.linkedin_url,
                    email=reveal.email,
                    phone=reveal.phone,
                )
                dup_error = _blocked(snap, reveal, existing)
                if dup_error:
                    _drop_unused_reveal(db, fresh_reveal)
                    results.append({"person_id": snap["person_id"], "name": label, "ok": False, "error": dup_error})
                    continue

        prev_count = int(campaign.prospect_count or 0)
        if already_paid:
            campaign.prospect_count = prev_count + 1
            db.flush()

        src = reveal if reveal is not None else None
        src_provider, src_person_id, src_external_id = svc.explorar_source_identity(snap)
        create = ProspectCreate(
            name=(src.name if src else label)[:255] or "—",
            company_name=((src.company_name if src else snap.get("company_name")) or "—")[:255],
            role=src.role if src else snap.get("role"),
            industry=src.industry if src else snap.get("industry"),
            industry_origin=snap.get("industry_origin"),
            company_size=snap.get("company_size"),
            size_origin=snap.get("size_origin"),
            country=src.country if src else snap.get("country"),
            linkedin_url=(src.linkedin_url if src else snap.get("linkedin_url")),
            email=src.email if src else None,
            phone=src.phone if src else None,
            whatsapp=src.phone if src else None,
            company_website=(
                f"https://{(src.company_domain if src else snap.get('company_domain'))}"
                if (src.company_domain if src else snap.get("company_domain"))
                else None
            ),
            source_provider=src_provider,
            source_external_id=src_external_id,
            source_person_id=src_person_id,
            notes="Sumado desde Explorar",
        )
        try:
            with db.begin_nested():
                row = _persist_new_prospect(db, campaign, create, released_ok=existing is not None)
        except HTTPException as e:
            if already_paid:
                campaign.prospect_count = prev_count
                db.flush()
            _drop_unused_reveal(db, fresh_reveal)
            results.append({"person_id": snap["person_id"], "name": label, "ok": False, "error": str(e.detail)})
            continue

        if reveal is not None:
            reveal.prospect_id = row.id
            reveal.campaign_id = campaign.id
        svc.remove_favorite(db, company_id=company_id, person_id=snap["person_id"])
        db.flush()
        try:
            with db.begin_nested():
                safe_upsert_from_prospect(db, row)
        except Exception:  # noqa: BLE001
            pass
        results.append(
            {
                "person_id": snap["person_id"],
                "name": label,
                "ok": True,
                "prospect_id": row.id,
                "paid_with": "reveal" if already_paid else "campaign_slot",
            }
        )

    db.commit()
    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "added": sum(1 for r in results if r.get("ok") and not r.get("already")),
        "results": results,
        "usage": svc.usage_summary(db, company_id, user.id),
    }
