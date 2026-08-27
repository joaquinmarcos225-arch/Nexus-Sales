"""Envío real por WhatsApp Business Cloud API (Meta Graph)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v21.0"
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def _reload_whatsapp_env() -> None:
    """Releer backend/.env para tokens WhatsApp rotados sin reiniciar uvicorn."""
    try:
        from dotenv import load_dotenv

        if _ENV_FILE.is_file():
            load_dotenv(_ENV_FILE, override=True)
    except Exception:
        logger.debug("whatsapp env reload skipped", exc_info=True)


def is_masked_contact(raw: str | None) -> bool:
    """Prospeo oculta email/teléfono con asteriscos (ej. m*******@corp.com, +54 9 342 6**-****)."""
    s = (raw or "").strip()
    if not s:
        return False
    if "*" in s or "#" in s:
        return True
    if re.search(r"x{2,}", s, flags=re.I):
        return True
    return False


def is_masked_phone(raw: str | None) -> bool:
    """Prospeo search-person devuelve móviles parcialmente ocultos (+54 9 342 6**-****)."""
    return is_masked_contact(raw)


def is_masked_email(raw: str | None) -> bool:
    return is_masked_contact(raw)


def sanitize_stored_email(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s or "@" not in s or is_masked_email(s):
        return None
    return s


def sanitize_stored_phone(raw: str | None) -> str | None:
    """None si el teléfono está enmascarado, vacío o sin dígitos suficientes para WhatsApp."""
    s = (raw or "").strip()
    if not s or is_masked_phone(s):
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) < 8:
        return None
    return s


def is_usable_phone(raw: str | None) -> bool:
    """True si el valor parece un móvil usable para WhatsApp."""
    from app.services.whatsapp_phone_validation import is_usable_whatsapp_mobile

    return is_usable_whatsapp_mobile(raw)


def normalize_whatsapp_digits(phone: str | None, whatsapp: str | None) -> str | None:
    from app.services.whatsapp_phone_validation import (
        digits_only,
        is_usable_whatsapp_mobile,
        is_whatsapp_mobile_digits,
        normalize_local_ar_to_e164,
    )

    raw = (whatsapp or phone or "").strip()
    if not raw or is_masked_phone(raw):
        return None
    digits = digits_only(raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) >= 10:
        digits = "54" + digits.lstrip("0")
    digits = normalize_local_ar_to_e164(digits)
    if not is_whatsapp_mobile_digits(digits):
        return None
    return digits


def meta_api_recipient_candidates(phone: str | None, whatsapp: str | None) -> list[str]:
    """Variantes E.164 a probar con Meta (AR suele diferir entre 54911…, 541115… y 5411…)."""
    digits = normalize_whatsapp_digits(phone, whatsapp)
    if not digits:
        return []
    primary = normalize_whatsapp_digits_for_meta_api(phone, whatsapp) or digits
    candidates: list[str] = []
    for value in (primary, digits):
        if value and value not in candidates:
            candidates.append(value)
    # +54 9 11 XXXXXXXX → también 54 11 XXXXXXXX (sin 9 ni 15)
    m = re.match(r"^54911(\d{8})$", digits)
    if m:
        short = f"5411{m.group(1)}"
        if short not in candidates:
            candidates.append(short)
    m15 = re.match(r"^541115(\d{8})$", primary or "")
    if m15:
        short = f"5411{m15.group(1)}"
        if short not in candidates:
            candidates.append(short)
    # Interior AR: +54 9 AREA+NUM (10 dígitos tras el 9) ↔ sin el 9 móvil
    # ej. 5493476362762 ↔ 543476362762 (WA Web a veces omite el 9).
    m9 = re.match(r"^549(\d{10})$", digits)
    if m9:
        without9 = f"54{m9.group(1)}"
        if without9 not in candidates:
            candidates.append(without9)
    m_no9 = re.match(r"^54(\d{10})$", digits)
    if m_no9 and not digits.startswith("549"):
        with9 = f"549{m_no9.group(1)}"
        if with9 not in candidates:
            candidates.append(with9)
    return candidates


def normalize_whatsapp_digits_for_meta_api(phone: str | None, whatsapp: str | None) -> str | None:
    """Formato E.164 que Meta Graph espera (p. ej. AR móvil 54911… → 541115…)."""
    digits = normalize_whatsapp_digits(phone, whatsapp)
    if not digits:
        return None
    if re.match(r"^54\d{2,4}15\d+$", digits):
        return digits
    # Argentina: +54 9 AREA NUM → +54 AREA 15 NUM (mismo celular, distinta representación).
    # Usar \g<1>: en replacement \115 es octal ('M'), no grupo 1 + "15".
    ar_rules = (
        (r"^54911(\d{8})$", r"541115\1"),
        (r"^549(\d{3})(\d{7})$", r"54\g<1>15\2"),
        (r"^549(\d{2})(\d{8})$", r"54\g<1>15\2"),
    )
    for pattern, repl in ar_rules:
        converted, count = re.subn(pattern, repl, digits)
        if count:
            return converted
    return digits


def whatsapp_config() -> dict[str, str]:
    _reload_whatsapp_env()
    return {
        "access_token": (os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip(),
        "phone_number_id": (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip(),
        "business_account_id": (os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID") or "").strip(),
        "api_version": (os.getenv("WHATSAPP_API_VERSION") or "v21.0").strip() or "v21.0",
        "dry_run": (os.getenv("WHATSAPP_DRY_RUN") or "").strip().lower() in ("1", "true", "yes", "on"),
        "use_cloud_api": (os.getenv("WHATSAPP_USE_CLOUD_API") or "").strip().lower()
        in ("1", "true", "yes", "on"),
        "template_default": (os.getenv("WHATSAPP_TEMPLATE_NAME") or "").strip(),
        "template_day7": (os.getenv("WHATSAPP_TEMPLATE_DAY7") or "").strip(),
        "template_day16": (os.getenv("WHATSAPP_TEMPLATE_DAY16") or "").strip(),
        "template_language": (os.getenv("WHATSAPP_TEMPLATE_LANGUAGE") or "es").strip() or "es",
    }


def template_name_for_sequence_day(day: int) -> str | None:
    """Plantilla Meta aprobada para contacto en frío (Día 7 / 16)."""
    cfg = whatsapp_config()
    per_day = {7: cfg["template_day7"], 16: cfg["template_day16"]}.get(int(day), "")
    name = (per_day or cfg["template_default"] or "").strip()
    return name or None


def template_language_code() -> str:
    return whatsapp_config()["template_language"]


def build_sequence_template_parameters(
    *,
    prospect_name: str | None,
    company_name: str | None,
    body: str,
) -> list[str]:
    """Parámetros {{1}}, {{2}}, … para plantilla de secuencia (nombre corto + empresa + mensaje)."""
    first = (prospect_name or "").strip().split()[0] if (prospect_name or "").strip() else "there"
    company = (company_name or "").strip() or "—"
    text = re.sub(r"\s+", " ", (body or "").strip())[:900]
    return [first[:60], company[:120], text]


def _post_whatsapp_message(*, payload: dict[str, Any], to_digits: str) -> dict[str, Any]:
    cfg = whatsapp_config()
    if cfg["dry_run"]:
        to = normalize_whatsapp_digits(to_digits, None)
        if not to:
            raise ValueError("Número de WhatsApp inválido")
        wamid = f"dry_run_wamid_{to[-6:]}"
        logger.info("whatsapp_dry_run to=%s type=%s", to, payload.get("type"))
        return {
            "whatsapp_message_id": wamid,
            "dry_run": True,
            "raw": {"dry_run": True, "to": to, "payload_type": payload.get("type")},
        }

    token = cfg["access_token"]
    phone_id = cfg["phone_number_id"]
    if not token or not phone_id:
        raise RuntimeError(
            "WhatsApp API no configurada. Completá WHATSAPP_ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID."
        )

    url = f"https://graph.facebook.com/{cfg['api_version']}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    candidates = meta_api_recipient_candidates(to_digits, None)
    if not candidates:
        raise ValueError("Número de WhatsApp inválido")

    last_detail = ""
    for to in candidates:
        logger.info("whatsapp_send to=%s type=%s", to, payload.get("type"))
        attempt = {**payload, "to": to}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=attempt, headers=headers)
        if resp.status_code < 400:
            data = resp.json()
            messages = data.get("messages") or []
            wamid = (messages[0].get("id") if messages else None) or None
            return {"whatsapp_message_id": wamid, "raw": data, "to": to}
        last_detail = resp.text[:500]
        if "131030" not in last_detail and "not in allowed list" not in last_detail.lower():
            logger.warning(
                "whatsapp_send_failed status=%s to=%s body=%s",
                resp.status_code,
                to,
                last_detail,
            )
            raise RuntimeError(f"WhatsApp API error {resp.status_code}: {last_detail}")
        logger.warning("whatsapp_send_131030 to=%s — probando otro formato", to)

    logger.warning("whatsapp_send_failed status=400 body=%s", last_detail)
    raise RuntimeError(f"WhatsApp API error 400: {last_detail}")


def send_template_message(
    *,
    to_digits: str,
    template_name: str,
    language_code: str,
    body_parameters: list[str],
) -> dict[str, Any]:
    """Envía plantilla Meta aprobada (contacto comercial en frío)."""
    name = (template_name or "").strip()
    if not name:
        raise ValueError("Nombre de plantilla WhatsApp vacío")
    lang = (language_code or "es").strip() or "es"
    params = [{"type": "text", "text": str(p)[:900]} for p in body_parameters if str(p).strip()]
    template: dict[str, Any] = {"name": name, "language": {"code": lang}}
    if params:
        template["components"] = [{"type": "body", "parameters": params}]
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "type": "template",
        "template": template,
    }
    return _post_whatsapp_message(payload=payload, to_digits=to_digits)


def send_sequence_whatsapp_message(
    *,
    to_digits: str,
    body: str,
    day: int,
    prospect_name: str | None = None,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Secuencia D7/D16: plantilla Meta si está configurada; si no, texto (ventana 24h)."""
    template_name = template_name_for_sequence_day(day)
    if template_name:
        return send_template_message(
            to_digits=to_digits,
            template_name=template_name,
            language_code=template_language_code(),
            body_parameters=build_sequence_template_parameters(
                prospect_name=prospect_name,
                company_name=company_name,
                body=body,
            ),
        )
    return send_text_message(to_digits=to_digits, body=body)


def is_whatsapp_dry_run() -> bool:
    return bool(whatsapp_config()["dry_run"])


def is_whatsapp_cloud_api_enabled() -> bool:
    """Opt-in: solo si WHATSAPP_USE_CLOUD_API=1 y hay tokens reales (no dry-run)."""
    cfg = whatsapp_config()
    if not cfg["use_cloud_api"] or cfg["dry_run"]:
        return False
    return bool(cfg["access_token"] and cfg["phone_number_id"])


def is_whatsapp_api_configured() -> bool:
    """True solo con Cloud API real habilitada. El producto default es WhatsApp Web asistido."""
    return is_whatsapp_cloud_api_enabled()


def verify_whatsapp_api(*, deep: bool = False) -> dict[str, Any]:
    cfg = whatsapp_config()
    # Producto actual: WhatsApp Web + extensión (como LinkedIn). Cloud API es opt-in.
    if not cfg["use_cloud_api"]:
        return {
            "configured": True,
            "api_reachable": True,
            "dry_run": False,
            "mode": "assisted",
            "phone_number_id": None,
            "display_phone_number": None,
            "verification_summary": (
                "WhatsApp Web asistido (extensión Nexus). Los toques salen por la cola manual, "
                "igual que LinkedIn — no usa Meta Cloud API."
            ),
        }
    if cfg["dry_run"]:
        return {
            "configured": False,
            "api_reachable": False,
            "dry_run": True,
            "mode": "dry_run",
            "phone_number_id": cfg["phone_number_id"] or "dry-run",
            "verification_summary": (
                "WHATSAPP_DRY_RUN está activo: desactivalo. El producto usa WhatsApp Web asistido "
                "o, si querés API oficial, WHATSAPP_USE_CLOUD_API=1 + tokens reales."
            ),
        }
    token = cfg["access_token"]
    phone_id = cfg["phone_number_id"]
    if not token or not phone_id:
        return {
            "configured": False,
            "api_reachable": False,
            "dry_run": False,
            "mode": "cloud_api",
            "verification_summary": "Faltan WHATSAPP_ACCESS_TOKEN y/o WHATSAPP_PHONE_NUMBER_ID en backend/.env",
        }
    if not deep:
        return {
            "configured": True,
            "api_reachable": True,
            "dry_run": False,
            "mode": "cloud_api",
            "phone_number_id": phone_id,
            "verification_summary": "Credenciales WhatsApp Cloud API presentes en el servidor.",
        }
    url = f"https://graph.facebook.com/{cfg['api_version']}/{phone_id}"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, params={"access_token": token, "fields": "id,display_phone_number"})
        ok = resp.status_code == 200
        data = resp.json() if ok else {}
        return {
            "configured": True,
            "api_reachable": ok,
            "dry_run": False,
            "mode": "cloud_api",
            "http_status": resp.status_code,
            "phone_number_id": phone_id,
            "display_phone_number": data.get("display_phone_number"),
            "verification_summary": (
                f"API WhatsApp accesible ({data.get('display_phone_number') or phone_id})"
                if ok
                else f"Error HTTP {resp.status_code}: {resp.text[:200]}"
            ),
            "api_error": None if ok else resp.text[:500],
        }
    except Exception as exc:
        return {
            "configured": True,
            "api_reachable": False,
            "dry_run": False,
            "mode": "cloud_api",
            "phone_number_id": phone_id,
            "verification_summary": f"No se pudo contactar la API de WhatsApp: {exc}",
            "api_error": str(exc),
        }


def send_text_message(*, to_digits: str, body: str) -> dict[str, Any]:
    """Envía mensaje de texto. Requiere ventana de 24h o plantilla aprobada en Meta."""
    text = (body or "").strip()
    if not text:
        raise ValueError("Mensaje WhatsApp vacío")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    return _post_whatsapp_message(payload=payload, to_digits=to_digits)
