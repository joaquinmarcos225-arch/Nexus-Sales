"""Cliente Gupshup WhatsApp (plantilla + sesión). Sin Meta Graph."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.services.whatsapp_cloud_service import (
    build_sequence_template_parameters,
    normalize_whatsapp_digits,
)

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
_TEMPLATE_URL = "https://api.gupshup.io/wa/api/v1/template/msg"
_SESSION_URL = "https://api.gupshup.io/wa/api/v1/msg"
_OPTIN_URL = "https://api.gupshup.io/sm/api/v1/app/opt/in/{app}"


def _reload_env() -> None:
    try:
        from dotenv import load_dotenv

        if _ENV_FILE.is_file():
            load_dotenv(_ENV_FILE, override=False)
    except Exception:
        logger.debug("gupshup env reload skipped", exc_info=True)


def gupshup_config() -> dict[str, Any]:
    _reload_env()
    provider = (os.getenv("WHATSAPP_PROVIDER") or "").strip().lower()
    return {
        "provider": provider,
        "api_key": (os.getenv("GUPSHUP_API_KEY") or "").strip(),
        "app_name": (os.getenv("GUPSHUP_APP_NAME") or "").strip(),
        "source": (os.getenv("GUPSHUP_SOURCE") or "").strip().lstrip("+"),
        "src_name": (os.getenv("GUPSHUP_SRC_NAME") or os.getenv("GUPSHUP_APP_NAME") or "CostGuard").strip(),
        "template_default": (os.getenv("GUPSHUP_TEMPLATE_ID") or "").strip(),
        "template_day7": (os.getenv("GUPSHUP_TEMPLATE_DAY7") or "").strip(),
        "template_day16": (os.getenv("GUPSHUP_TEMPLATE_DAY16") or "").strip(),
        "dry_run": (os.getenv("WHATSAPP_DRY_RUN") or "").strip().lower() in ("1", "true", "yes", "on"),
    }


def is_gupshup_provider() -> bool:
    return gupshup_config()["provider"] in ("gupshup", "gs")


def is_gupshup_ready() -> bool:
    """True si la secuencia debe salir por Gupshup (dry-run o credenciales)."""
    if not is_gupshup_provider():
        return False
    cfg = gupshup_config()
    if cfg["dry_run"]:
        return True
    return bool(cfg["api_key"] and cfg["source"])


def template_id_for_day(day: int) -> str | None:
    cfg = gupshup_config()
    per_day = {7: cfg["template_day7"], 16: cfg["template_day16"]}.get(int(day), "")
    name = (per_day or cfg["template_default"] or "").strip()
    return name or None


def _headers(api_key: str) -> dict[str, str]:
    return {
        "apikey": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }


def opt_in_destination(digits: str) -> None:
    """Alta técnica en Gupshup (no es un mensaje al prospecto)."""
    cfg = gupshup_config()
    if cfg["dry_run"] or not cfg["api_key"] or not cfg["app_name"]:
        return
    url = _OPTIN_URL.format(app=quote(cfg["app_name"], safe=""))
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                data={"user": digits},
                headers=_headers(cfg["api_key"]),
            )
        if resp.status_code >= 400:
            logger.warning("gupshup_optin_failed status=%s body=%s", resp.status_code, resp.text[:300])
    except Exception:
        logger.warning("gupshup_optin_error digits=%s", digits[-6:], exc_info=True)


def _parse_submit(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Gupshup no devolvió JSON ({resp.status_code}): {resp.text[:300]}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"Gupshup error {resp.status_code}: {resp.text[:500]}")
    status = str(data.get("status") or "").lower()
    message_id = str(data.get("messageId") or data.get("message_id") or "").strip()
    if status and status not in ("submitted", "success"):
        raise RuntimeError(f"Gupshup rechazó el envío: {data}")
    if not message_id:
        raise RuntimeError("Gupshup aceptó la solicitud pero no devolvió messageId.")
    return {"gupshup_message_id": message_id, "whatsapp_message_id": None, "dry_run": False, "raw": data}


def send_template_message(
    *,
    to_digits: str,
    template_id: str,
    body_parameters: list[str],
) -> dict[str, Any]:
    cfg = gupshup_config()
    dest = normalize_whatsapp_digits(to_digits, None) or to_digits
    dest = dest.lstrip("+")
    if cfg["dry_run"]:
        return {
            "gupshup_message_id": f"dry_gs_{uuid.uuid4().hex[:16]}",
            "whatsapp_message_id": f"dry_run_wamid_{dest[-6:]}",
            "dry_run": True,
            "raw": {"dry_run": True, "to": dest, "payload_type": "template"},
        }
    if not cfg["api_key"] or not cfg["source"]:
        raise RuntimeError("Gupshup no configurado. Completá GUPSHUP_API_KEY y GUPSHUP_SOURCE.")
    opt_in_destination(dest)
    form = {
        "source": cfg["source"],
        "destination": dest,
        "src.name": cfg["src_name"],
        "template": json.dumps({"id": template_id, "params": [str(p) for p in body_parameters]}),
    }
    logger.info("gupshup_template_send to=%s template=%s", dest[-6:], template_id[:12])
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_TEMPLATE_URL, data=form, headers=_headers(cfg["api_key"]))
    return _parse_submit(resp)


def send_session_text(*, to_digits: str, body: str) -> dict[str, Any]:
    cfg = gupshup_config()
    dest = normalize_whatsapp_digits(to_digits, None) or to_digits
    dest = dest.lstrip("+")
    text = (body or "").strip()
    if not text:
        raise ValueError("Mensaje WhatsApp vacío")
    if cfg["dry_run"]:
        return {
            "gupshup_message_id": f"dry_gs_{uuid.uuid4().hex[:16]}",
            "whatsapp_message_id": f"dry_run_wamid_{dest[-6:]}",
            "dry_run": True,
            "raw": {"dry_run": True, "to": dest, "payload_type": "text"},
        }
    if not cfg["api_key"] or not cfg["source"]:
        raise RuntimeError("Gupshup no configurado. Completá GUPSHUP_API_KEY y GUPSHUP_SOURCE.")
    form = {
        "channel": "whatsapp",
        "source": cfg["source"],
        "destination": dest,
        "src.name": cfg["src_name"],
        "message": json.dumps({"type": "text", "text": text[:4000]}),
    }
    logger.info("gupshup_session_send to=%s", dest[-6:])
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_SESSION_URL, data=form, headers=_headers(cfg["api_key"]))
    return _parse_submit(resp)


def send_sequence_whatsapp_via_gupshup(
    *,
    to_digits: str,
    body: str,
    day: int,
    prospect_name: str | None = None,
    company_name: str | None = None,
) -> dict[str, Any]:
    template_id = template_id_for_day(day)
    if template_id:
        return send_template_message(
            to_digits=to_digits,
            template_id=template_id,
            body_parameters=build_sequence_template_parameters(
                prospect_name=prospect_name,
                company_name=company_name,
                body=body,
            ),
        )
    cfg = gupshup_config()
    if not cfg["dry_run"]:
        raise ValueError(
            "Falta GUPSHUP_TEMPLATE_ID (o DAY7/DAY16). En frío Gupshup exige plantilla aprobada."
        )
    return send_session_text(to_digits=to_digits, body=body)
