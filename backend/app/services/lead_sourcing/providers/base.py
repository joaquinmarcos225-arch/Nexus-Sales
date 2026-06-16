"""Contratos de proveedores — sin acoplar a Apollo."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead


@dataclass
class ProviderStatus:
    name: str
    configured: bool
    message: str = ""


class LeadSourcingProvider(ABC):
    """Marcador base para implementaciones de sourcing."""

    name: str = "base"


class CompanySearchProvider(LeadSourcingProvider):
    """ICP → empresas candidatas (Web Search API)."""

    name: str = "web_search"

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def search_companies(
        self,
        campaign: Campaign,
        *,
        query: str | None = None,
        limit: int = 20,
    ) -> list[CompanyCandidateRead]:
        raise NotImplementedError


class PeopleExtractionProvider(LeadSourcingProvider):
    """Empresas/URLs → personas (PhantomBuster / LinkedIn)."""

    name: str = "phantombuster"

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def extract_people(
        self,
        campaign: Campaign,
        companies: list[CompanyCandidateRead],
        *,
        role_hint: str | None = None,
        limit: int = 50,
        phantom_queue: dict | None = None,
    ) -> list[LeadCandidateRead]:
        raise NotImplementedError


class ContactEnrichmentProvider(LeadSourcingProvider):
    """Solo leads con buen fit → email/tel/WhatsApp (Prospeo)."""

    name: str = "prospeo"

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def enrich_contact(self, lead: LeadCandidateRead) -> LeadCandidateRead:
        raise NotImplementedError


class ProviderNotConfiguredError(RuntimeError):
    pass


class ProviderAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.error_code = (error_code or "").strip() or None
