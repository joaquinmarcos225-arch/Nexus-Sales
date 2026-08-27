from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.connected_account import ConnectedAccount
    from app.models.company import Company
    from app.models.prospect import Prospect
    from app.models.seller_allocation import SellerCreditAllocation
    from app.models.team import Team


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("company_id", "email", name="uq_user_company_email"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Foto de perfil interna (equipo / UI). No se usa en copy de outreach.
    avatar_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship("Company", back_populates="users")
    team: Mapped[Team | None] = relationship("Team", back_populates="members")
    seller_allocation: Mapped[SellerCreditAllocation | None] = relationship(
        "SellerCreditAllocation",
        back_populates="seller",
        uselist=False,
    )
    campaigns_assigned: Mapped[list[Campaign]] = relationship(
        "Campaign", back_populates="seller"
    )
    connected_accounts: Mapped[list[ConnectedAccount]] = relationship(
        "ConnectedAccount", back_populates="user", cascade="all, delete-orphan"
    )
    owned_prospects: Mapped[list[Prospect]] = relationship(
        "Prospect",
        back_populates="owner",
        foreign_keys="Prospect.owner_user_id",
    )

    @property
    def role_enum(self) -> UserRole:
        from app.core.permissions import normalize_role

        return normalize_role(self.role)

    def sync_display_name(self) -> None:
        combined = f"{self.first_name} {self.last_name}".strip()
        self.name = combined or self.name
