"""
Personas «en uso» dentro de una empresa cliente de Nexus.

Mientras una campaña corriendo está prospectando a alguien, nadie más de la misma empresa cliente
puede sumarlo a otra campaña ni verlo en Explorar (salvo buscándolo por nombre, con aviso).
Tampoco a otra persona de la misma empresa prospecto: no se le escribe a una empresa desde dos lados.

Se libera cuando la campaña deja de correr, o cuando su secuencia terminó sin respuesta
o dijo que no le interesa. Si respondió, está interesado o tiene reunión, sigue en uso.
Quien pidió la baja nunca vuelve a aparecer para esa empresa cliente.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import CampaignStatus, ProspectStatus

_RELEASED_STATUSES = frozenset(
    {
        ProspectStatus.not_interested.value,
        ProspectStatus.failed.value,
        ProspectStatus.not_compatible.value,
    }
)
_ENGAGED_STATUSES = frozenset(
    {
        ProspectStatus.replied.value,
        ProspectStatus.interested.value,
        ProspectStatus.meeting_booked.value,
    }
)

_FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "hotmail.com.ar", "outlook.com",
        "outlook.es", "live.com", "live.com.ar", "msn.com", "yahoo.com", "yahoo.com.ar", "yahoo.es",
        "icloud.com", "me.com", "aol.com", "protonmail.com", "proton.me", "gmx.com", "zoho.com",
    }
)
_NON_COMPANY_DOMAINS = frozenset({"linkedin.com", "facebook.com", "instagram.com", "wa.me", "google.com"})
_GENERIC_COMPANY_NAMES = frozenset(
    {
        "", "-", "—", "na", "n/a", "none", "independiente", "independent", "freelance", "freelancer",
        "selfemployed", "autonomo", "consultorindependiente", "confidencial", "confidential", "stealth",
        "stealthstartup", "empresaconfidencial", "privado", "particular",
    }
)
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(s\.?\s?a\.?\s?s?|s\.?\s?r\.?\s?l\.?|s\.?\s?a\.?\s?c\.?|s\.?\s?p\.?\s?a\.?|s\.?\s?a\.?\s?u\.?|"
    r"ltda|inc|llc|ltd|corp|corporation|limited|gmbh|sl|sociedad anonima)\.?$"
)


def _fold(text: str | None) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower().strip()


def company_name_key(name: str | None) -> str | None:
    n = _fold(name)
    n = re.sub(r"[|(].*$", "", n).strip()
    for _ in range(2):
        n = _LEGAL_SUFFIX_RE.sub("", n).strip(" ,.-")
    key = re.sub(r"[^a-z0-9]", "", n)
    if len(key) < 3 or key in _GENERIC_COMPANY_NAMES:
        return None
    return key


def company_domain_key(domain_or_url: str | None) -> str | None:
    from app.services.nexus_contact_cache import normalize_domain

    dom = normalize_domain(domain_or_url)
    if not dom or dom in _FREE_MAIL_DOMAINS or dom in _NON_COMPANY_DOMAINS:
        return None
    return dom


def email_company_domain(email: str | None) -> str | None:
    em = (email or "").strip().lower()
    if "@" not in em:
        return None
    return company_domain_key(em.rsplit("@", 1)[1])


def is_opted_out_prospect(prospect: Any) -> bool:
    from app.services.prospect_opt_out import is_opted_out

    return bool(is_opted_out(prospect))


def is_released(prospect: Any) -> bool:
    """La persona ya no se está trabajando (aunque su campaña siga corriendo)."""
    from app.services.multichannel_sequence import SEQUENCE_GROUP_ENCAJONADO, SEQUENCE_GROUP_POSTERGADO, SEQUENCE_GROUP_REUNIONES

    status = (getattr(prospect, "status", None) or "").strip()
    group = (getattr(prospect, "sequence_group", None) or "").strip()
    if status in _RELEASED_STATUSES:
        return True
    if group == SEQUENCE_GROUP_ENCAJONADO:
        return True
    if getattr(prospect, "sequence_completed_at", None) is not None:
        if status in _ENGAGED_STATUSES or group in (SEQUENCE_GROUP_REUNIONES, SEQUENCE_GROUP_POSTERGADO):
            return False
        return True
    return False


def campaign_is_running(campaign: Any) -> bool:
    return (getattr(campaign, "status", None) or "") == CampaignStatus.running.value


def prospect_in_use(prospect: Any, campaign: Any) -> bool:
    return campaign is not None and campaign_is_running(campaign) and not is_released(prospect)


@dataclass(frozen=True)
class InUseInfo:
    prospect_id: int
    campaign_id: int
    campaign_name: str
    seller_name: str
    company_name: str

    def as_dict(self, *, reason: str) -> dict[str, Any]:
        return {
            "reason": reason,
            "prospect_id": self.prospect_id,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "seller_name": self.seller_name,
            "company_name": self.company_name,
        }


@dataclass
class InUseIndex:
    """Identidades en uso de una empresa cliente (para filtrar búsquedas sin consultar por fila)."""

    person_ids: dict[str, InUseInfo] = field(default_factory=dict)
    linkedin: dict[str, InUseInfo] = field(default_factory=dict)
    emails: dict[str, InUseInfo] = field(default_factory=dict)
    phones: dict[str, InUseInfo] = field(default_factory=dict)
    domains: dict[str, InUseInfo] = field(default_factory=dict)
    company_names: dict[str, InUseInfo] = field(default_factory=dict)
    opted_out_person_ids: set[str] = field(default_factory=set)
    opted_out_linkedin: set[str] = field(default_factory=set)
    contacted_person_ids: set[str] = field(default_factory=set)
    contacted_linkedin: set[str] = field(default_factory=set)

    def person_match(
        self,
        *,
        person_id: str | None = None,
        linkedin_url: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> InUseInfo | None:
        from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key
        from app.services.prospect_ingestion import phone_identity_keys

        pid = str(person_id or "").strip()
        if pid and pid in self.person_ids:
            return self.person_ids[pid]
        slug = linkedin_slug_key(linkedin_url) if linkedin_url else None
        if slug and slug in self.linkedin:
            return self.linkedin[slug]
        em = (email or "").strip().lower()
        if em and em in self.emails:
            return self.emails[em]
        for key in phone_identity_keys(phone, None) if phone else ():
            if key in self.phones:
                return self.phones[key]
        return None

    def company_match(
        self,
        *,
        company_domain: str | None = None,
        company_name: str | None = None,
        email: str | None = None,
    ) -> InUseInfo | None:
        for dom in (company_domain_key(company_domain), email_company_domain(email)):
            if dom and dom in self.domains:
                return self.domains[dom]
        name = company_name_key(company_name)
        if name and name in self.company_names:
            return self.company_names[name]
        return None

    def is_opted_out(self, *, person_id: str | None = None, linkedin_url: str | None = None) -> bool:
        from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key

        pid = str(person_id or "").strip()
        if pid and pid in self.opted_out_person_ids:
            return True
        slug = linkedin_slug_key(linkedin_url) if linkedin_url else None
        return bool(slug and slug in self.opted_out_linkedin)

    def contacted_before(self, *, person_id: str | None = None, linkedin_url: str | None = None) -> bool:
        from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key

        pid = str(person_id or "").strip()
        if pid and pid in self.contacted_person_ids:
            return True
        slug = linkedin_slug_key(linkedin_url) if linkedin_url else None
        return bool(slug and slug in self.contacted_linkedin)

    def blocking(self, hit: dict[str, Any]) -> dict[str, Any] | None:
        """Motivo por el que un resultado de búsqueda no se puede usar ahora (o None)."""
        pid = hit.get("person_id")
        li = hit.get("linkedin_url")
        if self.is_opted_out(person_id=pid, linkedin_url=li):
            return {"reason": "opt_out"}
        info = self.person_match(person_id=pid, linkedin_url=li, email=hit.get("email"), phone=hit.get("phone"))
        if info is not None:
            return info.as_dict(reason="person")
        info = self.company_match(
            company_domain=hit.get("company_domain"),
            company_name=hit.get("company_name"),
            email=hit.get("email"),
        )
        if info is not None:
            return info.as_dict(reason="company")
        return None


def build_in_use_index(
    db: Session,
    company_id: int,
    *,
    exclude_campaign_id: int | None = None,
) -> InUseIndex:
    """
    `exclude_campaign_id`: lo que ya está en esa campaña no bloquea (sumar a la misma campaña
    otra persona de una empresa que esa campaña ya trabaja es decisión de esa campaña).
    """
    from app.models.campaign import Campaign
    from app.models.prospect import Prospect
    from app.models.user import User
    from app.services.explorar import person_id_from_external_id
    from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key
    from app.services.prospect_ingestion import phone_identity_keys

    campaigns = {
        c.id: c for c in db.scalars(select(Campaign).where(Campaign.company_id == int(company_id))).all()
    }
    seller_ids = {c.seller_id for c in campaigns.values() if getattr(c, "seller_id", None)}
    sellers = {
        u.id: ((getattr(u, "first_name", None) or u.name or u.email or "").strip())
        for u in (db.scalars(select(User).where(User.id.in_(seller_ids))).all() if seller_ids else [])
    }

    index = InUseIndex()
    rows = db.scalars(select(Prospect).where(Prospect.company_id == int(company_id))).all()
    for p in rows:
        pid = (getattr(p, "source_person_id", None) or "").strip() or person_id_from_external_id(
            getattr(p, "source_external_id", None)
        )
        slug = linkedin_slug_key(p.linkedin_url) if p.linkedin_url else None
        if pid:
            index.contacted_person_ids.add(pid)
        if slug:
            index.contacted_linkedin.add(slug)
        if is_opted_out_prospect(p):
            if pid:
                index.opted_out_person_ids.add(pid)
            if slug:
                index.opted_out_linkedin.add(slug)
            continue
        if exclude_campaign_id is not None and int(p.campaign_id) == int(exclude_campaign_id):
            continue
        campaign = campaigns.get(p.campaign_id)
        if not prospect_in_use(p, campaign):
            continue
        info = InUseInfo(
            prospect_id=int(p.id),
            campaign_id=int(p.campaign_id),
            campaign_name=(campaign.name or "").strip() if campaign else "",
            seller_name=sellers.get(getattr(campaign, "seller_id", None), "") if campaign else "",
            company_name=(p.company_name or "").strip(),
        )
        if pid:
            index.person_ids.setdefault(pid, info)
        if slug:
            index.linkedin.setdefault(slug, info)
        em = (p.email or "").strip().lower()
        if em:
            index.emails.setdefault(em, info)
        for key in phone_identity_keys(p.phone, p.whatsapp):
            index.phones.setdefault(key, info)
        for dom in (company_domain_key(p.company_website), email_company_domain(p.email)):
            if dom:
                index.domains.setdefault(dom, info)
        name = company_name_key(p.company_name)
        if name:
            index.company_names.setdefault(name, info)
    return index


def blocking_message(block: dict[str, Any]) -> str:
    reason = block.get("reason")
    if reason == "opt_out":
        return "Esta persona pidió que no la contacten más. Nexus no la vuelve a prospectar."
    who = (block.get("seller_name") or "").strip()
    camp = (block.get("campaign_name") or "").strip()
    where = f"la campaña «{camp}»" if camp else "otra campaña"
    by = f" ({who})" if who else ""
    if reason == "company":
        company = (block.get("company_name") or "esa empresa").strip()
        return (
            f"Ya están prospectando a otra persona de {company} en {where}{by}. "
            "Se libera cuando esa campaña termine o esa persona deje de estar en curso."
        )
    return (
        f"La está prospectando {where}{by}. "
        "Se libera cuando esa campaña termine o su secuencia se cierre."
    )
