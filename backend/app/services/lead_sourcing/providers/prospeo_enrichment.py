"""Prospeo — enriquecimiento selectivo (email / móvil)."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.timeouts_config import PROSPEO_HTTP_TIMEOUT
from app.services.lead_sourcing.providers.base import (
    ContactEnrichmentProvider,
    ProviderAPIError,
    ProviderNotConfiguredError,
)

_PROSPEO_ENRICH_URL = "https://api.prospeo.io/enrich-person"


def _split_name(full: str) -> tuple[str | None, str | None, str | None]:
    text = (full or "").strip()
    if not text:
        return None, None, None
    parts = text.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:]), None
    return None, None, text


def _website_domain(url: str | None) -> str | None:
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        host = urlparse(raw).netloc or urlparse(raw).path
        host = host.lower().removeprefix("www.")
        return host or None
    except Exception:
        return None


class ProspeoEnrichmentProvider(ContactEnrichmentProvider):
    name: str = "prospeo"

    def is_configured(self) -> bool:
        return bool(getenv("PROSPEO_API_KEY"))

    def enrich_contact(self, lead: LeadCandidateRead) -> LeadCandidateRead:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                "Prospeo no configurado. Definí PROSPEO_API_KEY en backend/.env"
            )
        api_key = getenv("PROSPEO_API_KEY")
        headers = {"X-KEY": api_key, "Content-Type": "application/json"}

        first, last, full_name = _split_name(lead.name)
        data: dict = {}
        if lead.linkedin_url:
            data["linkedin_url"] = lead.linkedin_url.strip()
        elif first and last:
            data["first_name"] = first
            data["last_name"] = last
        elif full_name:
            data["full_name"] = full_name
        else:
            return lead

        if lead.company_name:
            data["company_name"] = lead.company_name
        domain = _website_domain(lead.company_website)
        if domain:
            data["company_website"] = domain
        if lead.email:
            data["email"] = lead.email

        needs_mobile = not (lead.phone or "").strip()
        # No gastar enrich_mobile si el teléfono que ya tenemos clasifica como fijo.
        if needs_mobile and (lead.phone or lead.whatsapp):
            from app.services.whatsapp_phone_validation import classify_phone_kind

            kind = classify_phone_kind(lead.phone or lead.whatsapp)
            if kind == "landline":
                needs_mobile = False
        body = {
            "only_verified_email": True,
            "enrich_mobile": needs_mobile,
            "data": data,
        }

        try:
            with httpx.Client(timeout=PROSPEO_HTTP_TIMEOUT) as client:
                resp = client.post(_PROSPEO_ENRICH_URL, headers=headers, json=body)
        except httpx.RequestError as e:
            raise ProviderAPIError(f"Prospeo: {e}", provider=self.name) from e

        payload = resp.json() if resp.text else {}
        if resp.status_code == 401:
            raise ProviderAPIError("Prospeo: API key inválida (401).", provider=self.name, status_code=401)
        if resp.status_code >= 400:
            code = payload.get("error_code") if isinstance(payload, dict) else None
            if code in ("NO_MATCH", "INVALID_DATAPOINTS"):
                return lead
            raise ProviderAPIError(
                f"Prospeo {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        if isinstance(payload, dict) and payload.get("error"):
            code = payload.get("error_code") or ""
            if code in ("NO_MATCH", "INVALID_DATAPOINTS"):
                return lead
            raise ProviderAPIError(
                f"Prospeo: {code or 'error'}",
                provider=self.name,
            )

        person = (payload.get("person") or {}) if isinstance(payload, dict) else {}
        email_obj = person.get("email") or {}
        email = lead.email
        if isinstance(email_obj, dict):
            revealed = email_obj.get("email") or email_obj.get("work_email")
            if revealed and (email_obj.get("revealed") is True or "@" in str(revealed)):
                email = str(revealed).strip() or email

        mobile_obj = person.get("mobile") or {}
        phone = lead.phone
        whatsapp = lead.whatsapp
        if isinstance(mobile_obj, dict):
            mob = mobile_obj.get("mobile") or mobile_obj.get("mobile_international")
            if mob:
                from app.services.whatsapp_phone_validation import (
                    sanitize_landline_phone,
                    sanitize_whatsapp_mobile,
                )

                wa = sanitize_whatsapp_mobile(str(mob))
                if wa:
                    phone = wa
                    whatsapp = wa
                else:
                    ll = sanitize_landline_phone(str(mob))
                    if ll:
                        phone = phone or ll
                    # No guardar fijo en whatsapp

        conf = 85 if email else (60 if phone else 45)
        return lead.model_copy(
            update={
                "email": email,
                "phone": phone,
                "whatsapp": whatsapp if whatsapp else None,
                "has_email": bool(email),
                "has_phone": bool(phone or lead.whatsapp),
                "enriched_by_prospeo": True,
                "enrichment_source": "prospeo",
                "enrichment_confidence": conf,
                "company_domain": domain or lead.company_domain,
            }
        )
