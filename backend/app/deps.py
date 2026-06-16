from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.models import Company
from app.models.campaign import Campaign
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User


def get_company(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Company:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return company


def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    if user.company_id != product.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este producto")
    return product


def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Campaign:
    campaign = db.get(Campaign, campaign_id, options=[joinedload(Campaign.product)])
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    if user.company_id != campaign.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta campaña")
    return campaign


def get_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Prospect:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=404, detail="Prospecto no encontrado")
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    return prospect
