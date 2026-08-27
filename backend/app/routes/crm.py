"""Integraciones CRM por empresa (OAuth + verificación)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.auth.deps import require_permission
from app.core.permissions import Permission
from app.database.session import get_db
from app.deps import get_company
from app.models.user import User
from app.schemas.crm import (
    CrmExclusionManualClearRead,
    CrmExclusionManualImportRead,
    CrmExclusionStatusRead,
    CrmExclusionSyncProviderRead,
    CrmExclusionSyncRead,
    CrmIntegrationVerifyRead,
    CrmSyncRetryRead,
    CrmSyncStatusRead,
)
from app.services.crm import company_credentials as cc
from app.services.crm import exclusions as crm_exclusions
from app.services.crm import hubspot, salesforce
from app.services.crm import sync as crm_sync

router = APIRouter(tags=["crm"])


@router.get(
    "/companies/{company_id}/integrations/hubspot/verify",
    response_model=CrmIntegrationVerifyRead,
)
def verify_hubspot_integration(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
    deep: bool = Query(True),
) -> CrmIntegrationVerifyRead:
    data = hubspot.verify_hubspot(db, company_id, deep=deep)
    return CrmIntegrationVerifyRead.model_validate(data)


@router.get(
    "/companies/{company_id}/integrations/salesforce/verify",
    response_model=CrmIntegrationVerifyRead,
)
def verify_salesforce_integration(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
    deep: bool = Query(True),
) -> CrmIntegrationVerifyRead:
    data = salesforce.verify_salesforce(db, company_id, deep=deep)
    return CrmIntegrationVerifyRead.model_validate(data)


@router.post("/companies/{company_id}/integrations/hubspot/disconnect")
def disconnect_hubspot(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> dict[str, bool]:
    ok = cc.disconnect_company_integration(db, company_id, cc.PROVIDER_HUBSPOT)
    db.commit()
    return {"ok": ok}


@router.post("/companies/{company_id}/integrations/salesforce/disconnect")
def disconnect_salesforce(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> dict[str, bool]:
    ok = cc.disconnect_company_integration(db, company_id, cc.PROVIDER_SALESFORCE)
    db.commit()
    return {"ok": ok}


@router.get(
    "/companies/{company_id}/integrations/crm/sync-status",
    response_model=CrmSyncStatusRead,
)
def crm_sync_status(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> CrmSyncStatusRead:
    data = crm_sync.company_sync_status(db, company_id)
    return CrmSyncStatusRead.model_validate(data)


@router.post(
    "/companies/{company_id}/integrations/crm/retry",
    response_model=CrmSyncRetryRead,
)
def crm_sync_retry(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> CrmSyncRetryRead:
    stats = crm_sync.retry_pending_for_company(db, company_id)
    db.commit()
    return CrmSyncRetryRead.model_validate(stats)


@router.get(
    "/companies/{company_id}/integrations/crm/exclusions",
    response_model=CrmExclusionStatusRead,
)
def crm_exclusion_status(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> CrmExclusionStatusRead:
    data = crm_exclusions.exclusion_status(db, company_id)
    return CrmExclusionStatusRead.model_validate(data)


@router.post(
    "/companies/{company_id}/integrations/crm/exclusions/sync",
    response_model=CrmExclusionSyncRead,
)
def crm_exclusion_sync(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
    provider: str | None = Query(None, description="hubspot | salesforce | omitir = ambos"),
) -> CrmExclusionSyncRead:
    providers = None
    if provider:
        p = provider.strip().lower()
        if p not in (cc.PROVIDER_HUBSPOT, cc.PROVIDER_SALESFORCE):
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="provider debe ser hubspot o salesforce")
        providers = [p]
    results = crm_exclusions.sync_exclusions_for_company(db, company_id, providers=providers)
    db.commit()
    status = crm_exclusions.exclusion_status(db, company_id)
    return CrmExclusionSyncRead(
        status=CrmExclusionStatusRead.model_validate(status),
        results=[CrmExclusionSyncProviderRead.model_validate(r.__dict__) for r in results],
    )


@router.post(
    "/companies/{company_id}/integrations/crm/exclusions/import",
    response_model=CrmExclusionManualImportRead,
)
async def crm_exclusion_manual_import(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
) -> CrmExclusionManualImportRead:
    """Importa exclusiones manuales (cuentas ya contactadas antes de Nexus)."""
    raw = ""
    if file is not None and file.filename:
        data = await file.read()
        try:
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw = data.decode("latin-1", errors="replace")
    elif text and text.strip():
        raw = text
    else:
        raise HTTPException(status_code=400, detail="Enviá un archivo CSV/TXT o el campo text")

    result = crm_exclusions.import_manual_exclusions_text(db, company_id, raw)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "No se pudo importar")
    db.commit()
    status = crm_exclusions.exclusion_status(db, company_id)
    return CrmExclusionManualImportRead(
        status=CrmExclusionStatusRead.model_validate(status),
        result=CrmExclusionSyncProviderRead.model_validate(result.__dict__),
    )


@router.delete(
    "/companies/{company_id}/integrations/crm/exclusions/manual",
    response_model=CrmExclusionManualClearRead,
)
def crm_exclusion_clear_manual(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> CrmExclusionManualClearRead:
    """Elimina exclusiones cargadas a mano (no afecta sync de HubSpot/Salesforce)."""
    deleted = crm_exclusions.clear_manual_exclusions(db, company_id)
    db.commit()
    status = crm_exclusions.exclusion_status(db, company_id)
    return CrmExclusionManualClearRead(
        status=CrmExclusionStatusRead.model_validate(status),
        deleted=deleted,
    )
