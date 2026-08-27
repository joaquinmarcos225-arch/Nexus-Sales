"""Completa canales faltantes de un prospecto manual según el plan de secuencia.

Lógica:
- Ancla = cualquier canal ya conocido (email, LinkedIn, WhatsApp/tel) o nombre+empresa.
- Solo busca canales que el plan necesita y que faltan.
- No pisa datos del usuario.
- Si Prospeo/Brave no encuentran match confiable → ese canal queda vacío y se omite.
- Si hay deadline_at, corta al vencimiento y deja lo hallado.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.prospect import Prospect
from app.services.lead_sourcing.linkedin_identity import (
    is_personal_linkedin_url,
    normalize_linkedin_url,
)
from app.services.whatsapp_cloud_service import is_masked_phone, sanitize_stored_email, sanitize_stored_phone

_logger = logging.getLogger(__name__)

# Confianza mínima si NO hay match nombre+empresa (teléfono solo / poco contexto).
_MIN_CONFIDENCE = 72
# Con nombre+empresa alineados, Prospeo a menudo da 62 (solo LinkedIn). Eso alcanza.
_NAME_COMPANY_MIN_CONFIDENCE = 55

_EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)


def _norm_name(raw: str | None) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def _is_provisional_name(raw: str | None) -> bool:
    """Nombre placeholder (solo canal cargado) — no usar para validar identidad."""
    n = _norm_name(raw)
    return (not n) or n in {
        "—",
        "-",
        "n/a",
        "contacto",
        "sin nombre",
        "prospecto",
        "contact",
    }


def _name_tokens(raw: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-záéíóúüñ]+", _norm_name(raw)) if len(t) >= 2}


def name_is_searchable(raw: str | None) -> bool:
    """Nombre real (nombre + apellido) usable como ancla de búsqueda."""
    return (not _is_provisional_name(raw)) and len(_name_tokens(raw)) >= 2


def _names_match(a: str | None, b: str | None) -> bool:
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    overlap = ta & tb
    return len(overlap) >= min(2, len(ta), len(tb)) and len(overlap) >= 2


def _company_match(a: str | None, b: str | None) -> bool:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb or na in {"—", "-", "n/a", "sin empresa"}:
        return True
    if na == nb:
        return True
    return na in nb or nb in na


def _split_name(full: str) -> tuple[str | None, str | None]:
    parts = [p for p in (full or "").strip().split() if p]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _channel_label_es(key: str) -> str:
    k = (key or "").strip().lower()
    if k == "email":
        return "Gmail"
    if k == "phone":
        return "WhatsApp"
    if k == "linkedin":
        return "LinkedIn"
    return k or "canal"


def format_channel_search_message(missing: Iterable[str]) -> str:
    """Mensaje mientras busca: 'Buscando Gmail y WhatsApp…'."""
    keys = [k for k in ("email", "phone", "linkedin") if k in set(missing)]
    labels = [_channel_label_es(k) for k in keys]
    if not labels:
        return "Buscando información de canales…"
    if len(labels) == 1:
        return f"Buscando {labels[0]}…"
    if len(labels) == 2:
        return f"Buscando {labels[0]} y {labels[1]}…"
    return f"Buscando {', '.join(labels[:-1])} y {labels[-1]}…"


def format_channel_find_summary(
    *,
    needed: Iterable[str] | None,
    prospect: Prospect,
    filled: dict[str, Any] | None = None,
    missing_after: Iterable[str] | None = None,
    enrich_status: str | None = None,
) -> str | None:
    """
    Resumen para la UI: 'Gmail encontrado · LinkedIn encontrado · WhatsApp no encontrado'.
    Siempre incluye los tres canales (encontrado / no encontrado), no solo los del plan.
    """
    status = (enrich_status or getattr(prospect, "channel_enrich_status", None) or "").strip().lower()
    # needed se acepta por compatibilidad de callers; la línea de resultado es siempre 3 canales.
    _ = needed
    show = ["email", "linkedin", "phone"]

    if status == "searching":
        still_missing = _missing_channels(prospect) & set(show)
        return format_channel_search_message(still_missing or show)

    # Resultado post-búsqueda (o estado actual de canales del plan).
    if status in ("none",) and not (filled or missing_after):
        # Aún no buscó: avisar qué falta cargar/buscar.
        bits = []
        for k in show:
            if k == "email":
                bits.append(
                    "Gmail cargado" if (prospect.email or "").strip() and "@" in (prospect.email or "")
                    else "Falta Gmail"
                )
            elif k == "phone":
                has = (k in filled) or bool(
                    sanitize_stored_phone(prospect.phone) or sanitize_stored_phone(prospect.whatsapp)
                )
                bits.append("WhatsApp cargado" if has else "Falta WhatsApp")
            elif k == "linkedin":
                bits.append(
                    "LinkedIn cargado"
                    if is_personal_linkedin_url(prospect.linkedin_url)
                    else "Falta LinkedIn"
                )
        return " · ".join(bits) if bits else None

    filled = filled or {}
    bits = []
    for k in show:
        if k == "email":
            ok = (k in filled) or (
                bool((prospect.email or "").strip()) and "@" in (prospect.email or "")
            )
            bits.append("Gmail encontrado" if ok else "Gmail no encontrado")
        elif k == "phone":
            ok = (k in filled) or bool(
                sanitize_stored_phone(prospect.phone) or sanitize_stored_phone(prospect.whatsapp)
            )
            bits.append("WhatsApp encontrado" if ok else "WhatsApp no encontrado")
        elif k == "linkedin":
            ok = (k in filled) or is_personal_linkedin_url(prospect.linkedin_url)
            bits.append("LinkedIn encontrado" if ok else "LinkedIn no encontrado")
    return " · ".join(bits) if bits else None


def channels_needed_from_sequence_plan(plan: dict[str, Any] | None) -> set[str]:
    """
    Canales que el plan pide completar: email | linkedin | phone.
    phone cubre WhatsApp.
    """
    needed: set[str] = set()
    if not isinstance(plan, dict):
        return needed

    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        ch = str(step.get("channel") or "").strip().lower()
        if ch == "email":
            needed.add("email")
        elif ch == "linkedin":
            needed.add("linkedin")
        elif ch in ("whatsapp", "wa", "phone", "call"):
            needed.add("phone")

    fu = plan.get("follow_up") or {}
    if isinstance(fu, dict) and fu.get("enabled"):
        fch = str(fu.get("channel") or "auto").strip().lower()
        if fch in ("email", "auto"):
            needed.add("email")
        if fch in ("whatsapp", "wa", "phone", "auto"):
            needed.add("phone")
        if fch in ("linkedin", "auto"):
            needed.add("linkedin")

    return needed


def _strip_masked_phones(prospect: Prospect) -> None:
    """Quita enmascarados y fijos/no-móviles que no sirven para WhatsApp."""
    from app.services.whatsapp_phone_validation import sanitize_whatsapp_mobile

    if is_masked_phone(prospect.phone):
        prospect.phone = None
    if is_masked_phone(prospect.whatsapp):
        prospect.whatsapp = None
    wa = sanitize_whatsapp_mobile(prospect.whatsapp)
    if prospect.whatsapp and not wa:
        prospect.whatsapp = None
    elif wa:
        prospect.whatsapp = wa
    phone = sanitize_stored_phone(prospect.phone)
    if phone and not sanitize_whatsapp_mobile(phone):
        prospect.phone = phone
    elif phone:
        prospect.phone = phone


def _resolve_person_with_full_mobile(
    person: dict[str, Any],
    *,
    need_phone: bool = False,
    need_email: bool = False,
) -> dict[str, Any]:
    """Si hay person_id y faltan canales, enrich-person (móvil completo / mail)."""
    from app.services.lead_sourcing.providers.prospeo_mvp import enrich_person_by_id
    from app.services.lead_sourcing.prospeo_phone import merge_contact_channels

    preview = merge_contact_channels(person)
    has_phone = bool(preview.get("phone"))
    has_email = bool((preview.get("email") or "").strip())
    want_phone = bool(need_phone) and not has_phone
    want_email = bool(need_email) and not has_email
    # Compat: sin flags explícitos, si falta phone → profundizar (como antes).
    if not need_phone and not need_email and not has_phone:
        want_phone = True
    if not want_phone and not want_email:
        return person

    pid = str(person.get("person_id") or person.get("id") or "").strip()
    if not pid:
        return person

    detailed: dict[str, Any] = {}
    try:
        detailed = enrich_person_by_id(pid, require_mobile=want_phone) or {}
    except Exception as exc:  # noqa: BLE001
        _logger.info("enrich-person for full mobile failed id=%s: %s", pid, exc)
        detailed = {}

    det_ch = merge_contact_channels(detailed) if isinstance(detailed, dict) else {}
    if want_phone and not det_ch.get("phone"):
        # Reintento sin only_verified_mobile, pero sí pidiendo revelar móvil.
        try:
            fallback = enrich_person_by_id(pid, require_mobile=False, enrich_mobile=True) or {}
        except Exception as exc:  # noqa: BLE001
            _logger.info("enrich-person fallback failed id=%s: %s", pid, exc)
            fallback = {}
        if isinstance(fallback, dict) and fallback:
            fb_ch = merge_contact_channels(fallback)
            if fb_ch.get("phone") or fb_ch.get("email") or not det_ch:
                detailed = fallback
                det_ch = fb_ch

    if isinstance(detailed, dict) and detailed and (
        det_ch.get("phone") or det_ch.get("email") or det_ch.get("linkedin_url")
    ):
        return detailed
    return person


def _ensure_company_website(prospect: Prospect, *, max_seconds: float = 6.0) -> str | None:
    """Resuelve dominio/web de la empresa si falta — ayuda a Prospeo mail."""
    existing = (getattr(prospect, "company_website", None) or "").strip()
    if existing:
        return existing
    name = (prospect.company_name or "").strip()
    if not name or name.lower() in {"—", "-", "n/a", "sin empresa"}:
        return None
    try:
        from uuid import uuid4

        from app.schemas.lead_sourcing import CompanyCandidateRead
        from app.services.lead_sourcing.company_name_normalizer import normalize_company_name
        from app.services.lead_sourcing.corporate_domain_resolver import (
            resolve_corporate_domain_for_company,
        )

        company = CompanyCandidateRead(
            external_id=f"manual-enrich-{uuid4().hex[:10]}",
            name=name,
            normalized_company_name=normalize_company_name(name) or name,
            website_url=None,
            country=(getattr(prospect, "country", None) or None),
        )
        deadline = time.monotonic() + max(2.0, float(max_seconds))
        res = resolve_corporate_domain_for_company(
            company,
            try_web_search=True,
            try_prospeo=True,
            fast_mode=True,
            deadline=deadline,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.info(
            "manual enrich domain resolve failed prospect=%s: %s",
            getattr(prospect, "id", None),
            exc,
        )
        return None
    if not res.resolved:
        return None
    web = (res.website_url or "").strip()
    if not web and res.domain:
        web = f"https://{res.domain}"
    if not web:
        return None
    prospect.company_website = web
    _logger.info(
        "manual enrich domain resolved prospect=%s domain=%s source=%s",
        getattr(prospect, "id", None),
        res.domain,
        res.source,
    )
    return web


def _company_domain_hint(prospect: Prospect) -> str | None:
    """Dominio usable para queries de email (sin esquema)."""
    from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

    web = (getattr(prospect, "company_website", None) or "").strip()
    dom = _website_domain(web) if web else None
    if dom:
        return dom
    return None


def _missing_channels(prospect: Prospect) -> set[str]:
    missing: set[str] = set()
    email = sanitize_stored_email(prospect.email)
    if not email:
        missing.add("email")
    if not is_personal_linkedin_url(prospect.linkedin_url):
        missing.add("linkedin")
    phone = sanitize_stored_phone(prospect.phone) or sanitize_stored_phone(prospect.whatsapp)
    if not phone:
        missing.add("phone")
    return missing


def _apply_phone(prospect: Prospect, phone: str, *, landline: str | None = None) -> None:
    from app.services.whatsapp_phone_validation import sanitize_landline_phone, sanitize_whatsapp_mobile

    wa = sanitize_whatsapp_mobile(phone)
    if wa:
        if not (prospect.phone or "").strip():
            prospect.phone = wa
        if not (prospect.whatsapp or "").strip():
            prospect.whatsapp = wa
    ll = sanitize_landline_phone(landline or (phone if not wa else None))
    if ll and not (getattr(prospect, "landline_phone", None) or "").strip():
        prospect.landline_phone = ll


def _person_display_name(person: dict[str, Any]) -> str:
    return str(
        person.get("full_name")
        or person.get("name")
        or " ".join(
            x
            for x in (
                person.get("first_name"),
                person.get("last_name"),
            )
            if x
        )
        or ""
    ).strip()


def _person_company(person: dict[str, Any]) -> str | None:
    for key in ("company_name", "current_company_name", "company"):
        val = person.get(key)
        if isinstance(val, dict):
            name = val.get("name") or val.get("company_name")
            if name:
                return str(name)
        elif val:
            return str(val)
    return None


def _same_linkedin(a: str | None, b: str | None) -> bool:
    na = normalize_linkedin_url(a)
    nb = normalize_linkedin_url(b)
    return bool(na and nb and na == nb)


def _past_deadline(deadline_at: Any | None) -> bool:
    if deadline_at is None:
        return False
    try:
        dl = deadline_at
        if isinstance(dl, str):
            dl = datetime.fromisoformat(dl.replace("Z", "+00:00"))
        if isinstance(dl, datetime):
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=UTC)
            return datetime.now(UTC) >= dl.astimezone(UTC)
    except Exception:  # noqa: BLE001
        return False
    return False


def _email_matches_person(email: str, name: str | None) -> bool:
    local = (email or "").split("@", 1)[0].lower()
    local = re.sub(r"[^a-záéíóúüñ0-9]", "", local)
    tokens = [t for t in re.split(r"[^a-záéíóúüñ]+", _norm_name(name)) if len(t) >= 2]
    if not local or not tokens:
        return False
    if any(t in local for t in tokens):
        return True
    if len(tokens) >= 2:
        initials = "".join(t[0] for t in tokens[:3])
        if initials and initials in local:
            return True
    return False


def _try_prospeo_search_person(
    prospect: Prospect, *, variant: int = 0, need_phone: bool = False
) -> dict[str, Any]:
    """Si enrich-person no resuelve: search-person por nombre y enrich por id."""
    from app.services.lead_sourcing.providers.prospeo_mvp import (
        _search_person_raw,
        enrich_person_by_id,
    )

    name = (prospect.name or "").strip()
    if not name_is_searchable(name):
        return {}
    company_raw = (prospect.company_name or "").strip()
    if company_raw in {"—", "-", "n/a", "sin empresa"}:
        company_raw = ""
    role = (getattr(prospect, "role", None) or "").strip()

    # Variantes por ronda: nombre+empresa, nombre+rol, solo nombre, first+last+empresa.
    if variant <= 0:
        query = f"{name} {company_raw}".strip()[:80] if company_raw else name[:80]
    elif variant == 1:
        query = f"{name} {role}".strip()[:80] if role else name[:80]
    elif variant == 2:
        query = name[:80]
    else:
        first, last = _split_name(name)
        base = f"{first} {last}".strip() or name
        query = f"{base} {company_raw}".strip()[:80] if company_raw else base[:80]
    try:
        hits, _err, _code, _status, _prev = _search_person_raw(
            filters={"person_name_or_job_title": query}
        )
    except Exception as exc:  # noqa: BLE001
        _logger.info("manual enrich search-person failed prospect=%s: %s", prospect.id, exc)
        return {}

    matched: list[dict[str, Any]] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        pname = _person_display_name(hit)
        if not _names_match(name, pname):
            continue
        pco = _person_company(hit)
        if company_raw and not _company_match(company_raw, pco):
            continue
        matched.append(hit)

    if not matched:
        return {}
    if len(matched) > 1 and not company_raw:
        return {}

    best = matched[0]
    pid = str(best.get("person_id") or best.get("id") or "").strip()
    if pid:
        try:
            detailed = enrich_person_by_id(pid, require_mobile=bool(need_phone))
            if isinstance(detailed, dict) and detailed:
                return detailed
            if need_phone:
                detailed = enrich_person_by_id(pid, require_mobile=False, enrich_mobile=True)
                if isinstance(detailed, dict) and detailed:
                    return detailed
        except Exception as exc:  # noqa: BLE001
            _logger.info(
                "manual enrich person-id failed prospect=%s id=%s: %s",
                prospect.id,
                pid,
                exc,
            )
    return best


def _try_prospeo_enrich(
    prospect: Prospect, missing: set[str], *, query_variant: int = 0
) -> dict[str, Any]:
    from app.services.lead_sourcing.providers.prospeo_mvp import (
        confidence_from_person,
        enrich_person_record,
    )
    from app.services.lead_sourcing.prospeo_contact_validation import is_forbidden_email
    from app.services.lead_sourcing.prospeo_phone import merge_contact_channels

    filled: dict[str, Any] = {}
    first, last = _split_name(prospect.name or "")
    anchor_li = normalize_linkedin_url(prospect.linkedin_url)
    anchor_email = sanitize_stored_email(prospect.email) or ""
    anchor_phone = sanitize_stored_phone(prospect.phone) or sanitize_stored_phone(prospect.whatsapp) or ""
    company_raw = (prospect.company_name or "").strip()
    if company_raw in {"—", "-", "n/a", "sin empresa"}:
        company_raw = ""

    # Anclas: LI | email | nombre+empresa | nombre+tel | tel | nombre+apellido.
    has_anchor = bool(anchor_li) or bool(anchor_email) or (
        bool((prospect.name or "").strip()) and bool(company_raw)
    ) or (bool((prospect.name or "").strip()) and bool(anchor_phone)) or bool(anchor_phone) or name_is_searchable(
        prospect.name
    )
    if not has_anchor:
        return filled

    # Sin web de empresa: resolver dominio ayuda a mail en Prospeo.
    if "email" in missing and not (prospect.company_website or "").strip() and company_raw:
        _ensure_company_website(prospect, max_seconds=6.0)

    person: dict[str, Any] = {}
    try:
        person = enrich_person_record(
            first_name=first,
            last_name=last,
            full_name=(prospect.name or "").strip() or None,
            company_name=company_raw or None,
            company_website=(prospect.company_website or "").strip() or None,
            linkedin_url=anchor_li,
            job_title=(getattr(prospect, "role", None) or "").strip() or None,
            email=anchor_email or None,
            mobile=anchor_phone or None,
            enrich_mobile=("phone" in missing),
            require_mobile=("phone" in missing),
        )
    except Exception as exc:  # noqa: BLE001
        _logger.info("manual enrich prospeo failed prospect=%s: %s", prospect.id, exc)
        person = {}

    if not isinstance(person, dict) or not person:
        person = _try_prospeo_search_person(
            prospect,
            variant=query_variant,
            need_phone="phone" in missing,
        )

    if isinstance(person, dict) and person:
        person = _resolve_person_with_full_mobile(
            person,
            need_phone="phone" in missing,
            need_email="email" in missing,
        )

    if not isinstance(person, dict) or not person:
        return filled

    linkedin_anchor_ok = bool(anchor_li) and _same_linkedin(anchor_li, person.get("linkedin_url"))
    channels_preview = merge_contact_channels(person)
    person_email = (channels_preview.get("email") or "").strip()
    email_anchor_ok = bool(anchor_email) and bool(person_email) and (
        anchor_email.lower() == person_email.lower()
    )
    # Si enriquecimos por email y Prospeo no devolvió el mismo email pero sí perfil, aceptar.
    if bool(anchor_email) and not email_anchor_ok and (person.get("linkedin_url") or person_email):
        email_anchor_ok = True

    pname = _person_display_name(person)
    name_company_ok = bool(
        pname
        and name_is_searchable(prospect.name)
        and _names_match(prospect.name, pname)
        and (not company_raw or _company_match(company_raw, _person_company(person)))
    )

    # Identidad: LinkedIn o email ancla ganan; si no, exigir nombre real (+ empresa si hay).
    if not linkedin_anchor_ok and not email_anchor_ok:
        if (
            pname
            and not _is_provisional_name(prospect.name)
            and not _names_match(prospect.name, pname)
        ):
            return filled
        if company_raw and not _company_match(company_raw, _person_company(person)):
            return filled
        # Solo teléfono / poco contexto: exigir confianza alta.
        conf_early = confidence_from_person(person)
        if _is_provisional_name(prospect.name) and conf_early < _MIN_CONFIDENCE:
            return filled
        if not name_company_ok and conf_early < _MIN_CONFIDENCE:
            return filled

    conf = confidence_from_person(person)
    min_ok = _NAME_COMPANY_MIN_CONFIDENCE if name_company_ok else _MIN_CONFIDENCE
    if not linkedin_anchor_ok and not email_anchor_ok and conf < min_ok:
        return filled

    # Completar empresa/rol/nombre vacíos desde Prospeo; no pisa lo del usuario.
    pcompany = _person_company(person)
    if pcompany and (not (prospect.company_name or "").strip() or (prospect.company_name or "").strip() in {"—", "-"}):
        prospect.company_name = pcompany
        filled["company_name"] = pcompany
    prole = (
        str(person.get("current_job_title") or person.get("job_title") or "").strip() or None
    )
    if prole and not (getattr(prospect, "role", None) or "").strip():
        prospect.role = prole
        filled["role"] = prole
    if pname and _is_provisional_name(prospect.name):
        prospect.name = pname
        filled["name"] = pname

    channels = channels_preview
    from app.services.whatsapp_phone_validation import sanitize_landline_phone, sanitize_whatsapp_mobile

    email = (channels.get("email") or "").strip() or None
    mobile = sanitize_whatsapp_mobile(channels.get("whatsapp_number") or channels.get("mobile_phone"))
    landline = sanitize_landline_phone(channels.get("landline_phone"))
    li = normalize_linkedin_url(channels.get("linkedin_url") or person.get("linkedin_url"))

    if "email" in missing and email and "@" in email and not is_forbidden_email(email):
        prospect.email = email
        filled["email"] = email
        missing.discard("email")
    if "linkedin" in missing and li and is_personal_linkedin_url(li):
        prospect.linkedin_url = li
        filled["linkedin"] = li
        missing.discard("linkedin")
    if "phone" in missing and (mobile or landline):
        if mobile:
            _apply_phone(prospect, mobile)
            filled["phone"] = mobile
        elif landline:
            _apply_phone(prospect, landline, landline=landline)
            filled["phone"] = landline
        missing.discard("phone")

    if filled:
        filled["source"] = "prospeo"
        filled["confidence"] = conf
        if linkedin_anchor_ok:
            filled["identity"] = "linkedin_anchor"
        elif email_anchor_ok:
            filled["identity"] = "email_anchor"
        elif anchor_phone:
            filled["identity"] = "phone_anchor"
        else:
            filled["identity"] = "name_company"
    return filled


def _try_brave_linkedin(prospect: Prospect, *, variant: int = 0) -> dict[str, Any]:
    li = _search_brave_linkedin_url(prospect, variant=variant)
    if not li:
        return {}
    prospect.linkedin_url = li
    return {"linkedin": li, "source": "brave", "confidence": 80, "query_variant": variant}


def _search_brave_linkedin_url(prospect: Prospect, *, variant: int = 0) -> str | None:
    """Busca URL de LinkedIn personal sin mutar el prospecto (seguro en thread)."""
    from app.services.lead_sourcing.providers.base import ProviderAPIError
    from app.services.lead_sourcing.providers.web_search_backends import search_web

    name = (prospect.name or "").strip()
    if not name:
        return None
    company = (prospect.company_name or "").strip()
    if company in {"—", "-", ""}:
        company = ""
    role = (getattr(prospect, "role", None) or "").strip()

    # Variantes de query: no rendirse con una sola búsqueda.
    if variant <= 0:
        query = f'"{name}"'
        if company:
            query += f' "{company}"'
        if role:
            query += f' "{role}"'
        query += " site:linkedin.com/in"
    elif variant == 1:
        query = f'"{name}"'
        if company:
            query += f' "{company}"'
        query += " site:linkedin.com/in"
    elif variant == 2:
        query = f"{name} {company} LinkedIn".strip()
    else:
        query = f'"{name}" site:linkedin.com/in'

    try:
        hits = search_web(query, limit=8, provider="manual_prospect_enrich")
    except ProviderAPIError as exc:
        _logger.info("manual enrich brave skipped prospect=%s: %s", prospect.id, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        _logger.info("manual enrich brave failed prospect=%s: %s", prospect.id, exc)
        return None

    name_l = _norm_name(name)
    for url, title, snippet in hits:
        li = normalize_linkedin_url(url)
        if not li or not is_personal_linkedin_url(li):
            continue
        blob = _norm_name(f"{title} {snippet}")
        if not _names_match(name, title) and name_l not in blob:
            continue
        if company and not _company_match(company, title) and _norm_name(company) not in blob:
            # En variantes más amplias (2+) ser un poco más permisivo si el nombre matchea fuerte.
            if variant < 2 or not _names_match(name, title):
                continue
        return li
    return None


def _try_brave_email(prospect: Prospect, *, variant: int = 0) -> dict[str, Any]:
    """Último recurso: email público en la web que coincida con el nombre."""
    from app.services.lead_sourcing.prospeo_contact_validation import is_forbidden_email
    from app.services.lead_sourcing.providers.base import ProviderAPIError
    from app.services.lead_sourcing.providers.web_search_backends import search_web

    name = (prospect.name or "").strip()
    if not name_is_searchable(name):
        return {}
    company = (prospect.company_name or "").strip()
    if company in {"—", "-", ""}:
        company = ""
    domain = _company_domain_hint(prospect)

    if variant <= 0 and domain:
        query = f'"{name}" @{domain} OR email:{domain}'
    elif variant <= 0:
        query = f'"{name}"'
        if company:
            query += f' "{company}"'
        query += " email OR correo"
    elif variant == 1 and domain:
        query = f'"{name}" site:{domain} email OR contacto OR contact'
    elif company:
        first_tok = company.split()[0]
        query = f'"{name}" @{first_tok}' if first_tok else f'"{name}" email'
    else:
        query = f'"{name}" email'

    try:
        hits = search_web(query, limit=8, provider="manual_prospect_enrich_email")
    except ProviderAPIError as exc:
        _logger.info("manual enrich brave-email skipped prospect=%s: %s", prospect.id, exc)
        return {}
    except Exception as exc:  # noqa: BLE001
        _logger.info("manual enrich brave-email failed prospect=%s: %s", prospect.id, exc)
        return {}

    name_l = _norm_name(name)
    for url, title, snippet in hits:
        blob = f"{title} {snippet} {url}"
        blob_l = _norm_name(blob)
        if not _names_match(name, title) and name_l not in blob_l:
            continue
        if company and not _company_match(company, title) and _norm_name(company) not in blob_l:
            continue
        for raw_em in _EMAIL_RE.findall(blob):
            email = raw_em.strip().lower()
            if is_forbidden_email(email):
                continue
            if not _email_matches_person(email, name):
                continue
            prospect.email = email
            return {"email": email, "source": "brave", "confidence": 70, "query_variant": variant}
    return {}


def _seconds_left(deadline_at: Any | None) -> float | None:
    if deadline_at is None:
        return None
    try:
        dl = deadline_at
        if isinstance(dl, str):
            dl = datetime.fromisoformat(dl.replace("Z", "+00:00"))
        if isinstance(dl, datetime):
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=UTC)
            return (dl.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    except Exception:  # noqa: BLE001
        return None
    return None


def enrich_missing_channels(
    db: Session,
    prospect: Prospect,
    *,
    needed_channels: Iterable[str] | None = None,
    sequence_plan: dict[str, Any] | None = None,
    deadline_at: Any | None = None,
) -> dict[str, Any]:
    """
    Completa email / LinkedIn / teléfono faltantes con alta confianza.

    Si `needed_channels` o `sequence_plan` vienen, solo busca lo que el plan necesita.
    No pisa datos ya cargados. No escribe si no hay match claro.

    Con `deadline_at`: reintenta (Prospeo + Brave) mientras quede tiempo y falten canales.
    Sin deadline (kickoff final): 1–2 intentos cortos, sin bloquear minutos.
    """
    del db  # reserved for future ledger / audit rows

    missing = _missing_channels(prospect)
    if needed_channels is not None:
        wanted = {str(c).strip().lower() for c in needed_channels if str(c).strip()}
        if "whatsapp" in wanted or "wa" in wanted:
            wanted.add("phone")
        missing &= wanted
    elif sequence_plan is not None:
        needed = channels_needed_from_sequence_plan(sequence_plan)
        if needed:
            missing &= needed

    if not missing:
        return {
            "filled": {},
            "missing_before": [],
            "missing_after": [],
            "needed": [],
            "skipped_reason": "nothing_needed",
        }

    _strip_masked_phones(prospect)

    # Resolver dominio temprano: Prospeo + Brave email lo usan en la 1.ª ronda.
    if "email" in missing and not (getattr(prospect, "company_website", None) or "").strip():
        _ensure_company_website(prospect, max_seconds=6.0)

    before = sorted(missing)
    filled: dict[str, Any] = {}

    if _past_deadline(deadline_at):
        return {
            "filled": filled,
            "missing_before": before,
            "missing_after": before,
            "needed": before,
            "timed_out": True,
        }

    # Background: pocas rondas densas (paralelo Prospeo+Brave). Kickoff sync: 2 cortas.
    if deadline_at is None:
        soft_deadline = datetime.now(UTC) + timedelta(seconds=35)
        effective_deadline: Any = soft_deadline
        max_rounds = 2
        sleep_sec = 1.2
    else:
        effective_deadline = deadline_at
        max_rounds = 4
        sleep_sec = 1.2

    def _merge(chunk: dict[str, Any]) -> None:
        for k, v in chunk.items():
            if k in ("email", "linkedin", "phone") and v:
                filled[k] = v

    def _apply_brave_li(url: str | None) -> bool:
        if not url or "linkedin" not in missing:
            return False
        prospect.linkedin_url = url
        filled["linkedin"] = url
        missing.discard("linkedin")
        _logger.info(
            "manual enrich round brave linkedin prospect=%s",
            getattr(prospect, "id", None),
        )
        return True

    round_idx = 0
    while missing and round_idx < max_rounds and not _past_deadline(effective_deadline):
        left = _seconds_left(effective_deadline)
        if left is not None and left < 1.5:
            break

        need_brave_li = "linkedin" in missing and name_is_searchable(prospect.name)
        # Snapshot: Brave corre en thread sin mutar; Prospeo muta el prospecto.
        brave_snap = SimpleNamespace(
            id=getattr(prospect, "id", None),
            name=prospect.name,
            company_name=prospect.company_name,
            role=getattr(prospect, "role", None),
        )

        brave_url: str | None = None
        prospeo: dict[str, Any] = {}
        if need_brave_li:
            # LI-first + paralelo: Brave y Prospeo a la vez; si Brave gana LI, re-Prospeo.
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_brave = pool.submit(
                    _search_brave_linkedin_url, brave_snap, variant=round_idx
                )
                fut_prospeo = pool.submit(
                    _try_prospeo_enrich,
                    prospect,
                    missing,
                    query_variant=round_idx,
                )
                prospeo = fut_prospeo.result() or {}
                brave_url = fut_brave.result()
        else:
            prospeo = _try_prospeo_enrich(
                prospect, missing, query_variant=round_idx
            ) or {}

        if prospeo:
            _merge(prospeo)
            _logger.info(
                "manual enrich round=%s prospeo filled=%s prospect=%s",
                round_idx,
                sorted(k for k in prospeo if k in ("email", "linkedin", "phone")),
                getattr(prospect, "id", None),
            )

        got_li_from_brave = _apply_brave_li(brave_url)
        if got_li_from_brave and missing & {"email", "phone"} and not _past_deadline(
            effective_deadline
        ):
            again = _try_prospeo_enrich(
                prospect, missing, query_variant=round_idx
            )
            if again:
                _merge(again)

        if _past_deadline(effective_deadline) or not missing:
            break

        if "email" in missing and not _past_deadline(effective_deadline):
            brave_em = _try_brave_email(prospect, variant=round_idx)
            if brave_em.get("email"):
                filled["email"] = brave_em["email"]
                missing.discard("email")
                _logger.info(
                    "manual enrich round=%s brave email prospect=%s",
                    round_idx,
                    getattr(prospect, "id", None),
                )

        if not missing:
            break

        round_idx += 1
        if round_idx >= max_rounds:
            break
        left = _seconds_left(effective_deadline)
        if left is None or left < sleep_sec + 1.0:
            break
        time.sleep(min(sleep_sec, max(0.5, left - 1.0)))

    still_needed = sorted(missing)
    timed_out = bool(_past_deadline(effective_deadline)) and bool(still_needed)
    return {
        "filled": filled,
        "missing_before": before,
        "missing_after": still_needed,
        "needed": before,
        "timed_out": timed_out,
        "rounds": round_idx + 1,
    }


def enrich_prospect_for_sequence_plan(
    db: Session,
    prospect: Prospect,
    *,
    sequence_plan: dict[str, Any] | None,
    deadline_at: Any | None = None,
) -> dict[str, Any]:
    """Atajo: enrich solo canales que pide el plan de secuencia."""
    return enrich_missing_channels(
        db,
        prospect,
        sequence_plan=sequence_plan,
        deadline_at=deadline_at,
    )
