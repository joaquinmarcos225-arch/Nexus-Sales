from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import RequireProductCreate, RequireProductEdit, RequireProductDelete, get_current_user
from app.database.session import get_db
from app.deps import get_company, get_product
from app.models.company import Company
from app.models.product import Product
from app.models.user import User
from app.schemas.product import (
    ProductCreate,
    ProductDocumentExtractRead,
    ProductInterpretRead,
    ProductInterpretRequest,
    ProductInterpretRootRequest,
    ProductRead,
    ProductUpdate,
)
from app.services import openai_service
from app.services.product_document_extract import DocumentExtractError, extract_document_text

router = APIRouter(tags=["products"])


@router.get("/companies/{company_id}/products", response_model=list[ProductRead])
def list_company_products(
    company_id: int,
    include_inactive: Annotated[bool, Query(description="Si true incluye borrados lógicos")] = False,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    _user: User = Depends(get_current_user),
) -> list[Product]:
    stmt = select(Product).where(Product.company_id == company_id).order_by(Product.id)
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    return list(db.scalars(stmt).all())


def _compose_target_notes_block(parts: dict[str, str]) -> str:
    blocks: list[str] = []
    if parts.get("target_audience"):
        blocks.append(f"Público objetivo\n{parts['target_audience']}")
    if parts.get("use_cases"):
        blocks.append(f"Casos de uso\n{parts['use_cases']}")
    if parts.get("pain_points"):
        blocks.append(f"Problemas que resuelve / pain points\n{parts['pain_points']}")
    if parts.get("main_benefits"):
        blocks.append(f"Beneficios principales\n{parts['main_benefits']}")
    if parts.get("common_objections"):
        blocks.append(f"Objeciones comunes\n{parts['common_objections']}")
    if parts.get("recommended_tone"):
        blocks.append(f"Tono recomendado\n{parts['recommended_tone']}")
    return "\n\n".join(blocks).strip()


def _truncate_field(s: str, max_len: int) -> str:
    """Evita respuestas desmesuradas de la IA; la DB admite Text pero mantenemos límites razonables."""
    t = (s or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _interpret_to_read(data: dict[str, str]) -> ProductInterpretRead:
    target_notes = _compose_target_notes_block(data)
    return ProductInterpretRead(
        suggested_name=(data.get("suggested_name") or data.get("name") or "Producto")[:255],
        description=_truncate_field(data.get("description") or "", 24_000),
        value_proposition=_truncate_field(data.get("value_proposition") or "", 12_000),
        target_notes=_truncate_field(target_notes, 48_000),
        benefits=_truncate_field(data.get("main_benefits") or "", 8_000),
        pain_points=_truncate_field(data.get("pain_points") or "", 8_000),
        objections=_truncate_field(data.get("common_objections") or "", 8_000),
        recommended_tone=_truncate_field(data.get("recommended_tone") or "", 2_000),
        use_cases=_truncate_field(data.get("use_cases") or "", 12_000),
    )


@router.post(
    "/companies/{company_id}/products/extract-document",
    response_model=ProductDocumentExtractRead,
)
async def extract_company_product_document(
    company_id: int,
    _user: RequireProductEdit,
    file: UploadFile = File(...),
    _company=Depends(get_company),
) -> ProductDocumentExtractRead:
    """Extrae texto de PDF / DOCX / TXT / MD / CSV / HTML / JSON para el flujo de producto."""
    data = await file.read()
    try:
        extracted = extract_document_text(
            filename=file.filename or "documento",
            data=data,
            content_type=file.content_type,
        )
    except DocumentExtractError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return ProductDocumentExtractRead(
        text=extracted.text,
        filename=extracted.filename,
        format=extracted.format,
        chars=extracted.chars,
    )


@router.post("/products/interpret", response_model=ProductInterpretRead)
def interpret_product_global(
    payload: ProductInterpretRootRequest,
    user: RequireProductEdit,
    db: Session = Depends(get_db),
) -> ProductInterpretRead:
    """Interpretación de producto desde texto largo (`raw_text`); requiere `company_id` válido."""
    if user.company_id != payload.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    if db.get(Company, payload.company_id) is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    data = openai_service.interpret_product_document(payload.raw_text)
    return _interpret_to_read(data)


@router.post(
    "/companies/{company_id}/products/interpret",
    response_model=ProductInterpretRead,
)
def interpret_company_product_document(
    company_id: int,
    payload: ProductInterpretRequest,
    _user: RequireProductEdit,
    _company=Depends(get_company),
) -> ProductInterpretRead:
    data = openai_service.interpret_product_document(payload.document_text)
    return _interpret_to_read(data)


@router.post("/companies/{company_id}/products", response_model=ProductRead, status_code=201)
def create_company_product(
    company_id: int,
    payload: ProductCreate,
    _user: RequireProductCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> Product:
    product = Product(
        company_id=company_id,
        name=payload.name,
        description=payload.description,
        value_proposition=payload.value_proposition,
        target_notes=payload.target_notes,
        market_scope=payload.market_scope.value,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    payload: ProductUpdate,
    _user: RequireProductEdit,
    db: Session = Depends(get_db),
    product: Product = Depends(get_product),
) -> Product:
    data = payload.model_dump(exclude_unset=True)
    if "market_scope" in data and data["market_scope"] is not None:
        scope = data["market_scope"]
        data["market_scope"] = scope.value if hasattr(scope, "value") else str(scope)
    for k, v in data.items():
        setattr(product, k, v)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{product_id}", response_model=ProductRead)
def soft_delete_product(
    _user: RequireProductDelete,
    db: Session = Depends(get_db),
    product: Product = Depends(get_product),
) -> Product:
    product.is_active = False
    db.commit()
    db.refresh(product)
    return product
