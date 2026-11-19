"""Estado del motor WhatsApp (Go whatsmeow vs simulate local) para UI y guards."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.services.whatsapp_meow_client import get_meow_engine_client


def _simulate_env_enabled() -> bool:
    return (os.getenv("NEXUS_WHATSAPP_SIMULATE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def probe_meow_engine_status() -> dict[str, Any]:
    """Describe cómo está configurado el pairing (sin enviar mensajes ni escanear QR)."""
    meow_url = (os.getenv("NEXUS_WHATSAPP_MEOW_URL") or "").strip().rstrip("/")
    simulate_env = _simulate_env_enabled()
    token = (os.getenv("NEXUS_WHATSAPP_INTERNAL_TOKEN") or "").strip()

    if not meow_url:
        if simulate_env:
            return {
                "engine": "local_simulate",
                "go_configured": False,
                "go_reachable": False,
                "go_simulate": True,
                "can_pair_real_qr": False,
                "dev_simulate_tools": True,
                "user_message": (
                    "Modo desarrollo: el backend usa el motor simulate local (sin Go). "
                    "El QR será de prueba (prefijo SIM-), no WhatsApp real. "
                    "Para QR auténtico: levantá nexus-whatsapp sin NEXUS_WHATSAPP_SIMULATE "
                    "y configurá NEXUS_WHATSAPP_MEOW_URL apuntando al servicio."
                ),
            }
        return {
            "engine": "unconfigured",
            "go_configured": False,
            "go_reachable": False,
            "go_simulate": False,
            "can_pair_real_qr": False,
            "dev_simulate_tools": False,
            "user_message": (
                "WhatsApp no está conectado al motor Go. "
                "Iniciá el servicio nexus-whatsapp (whatsmeow) y definí NEXUS_WHATSAPP_MEOW_URL "
                "en el entorno del backend (mismo token interno en ambos procesos)."
            ),
        }

    if not token:
        return {
            "engine": "misconfigured",
            "go_configured": True,
            "go_reachable": False,
            "go_simulate": False,
            "can_pair_real_qr": False,
            "dev_simulate_tools": False,
            "user_message": (
                "Falta NEXUS_WHATSAPP_INTERNAL_TOKEN en el backend Python "
                "(debe coincidir con el servicio Go)."
            ),
        }

    headers = {"X-Nexus-Internal-Token": token}
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{meow_url}/health", headers=headers)
    except Exception as exc:
        return {
            "engine": "go_unreachable",
            "go_configured": True,
            "go_reachable": False,
            "go_simulate": simulate_env,
            "can_pair_real_qr": False,
            "dev_simulate_tools": simulate_env,
            "user_message": (
                f"No se pudo contactar el motor Go en {meow_url}: {exc}. "
                "Verificá que nexus-whatsapp esté en ejecución."
            ),
        }

    if resp.status_code >= 400:
        return {
            "engine": "go_error",
            "go_configured": True,
            "go_reachable": False,
            "go_simulate": simulate_env,
            "can_pair_real_qr": False,
            "dev_simulate_tools": simulate_env,
            "user_message": (
                f"Motor Go respondió HTTP {resp.status_code}. "
                "Revisá token interno y URL."
            ),
        }

    payload = resp.json() if resp.content else {}
    go_simulate = bool(payload.get("simulate"))
    can_real = not go_simulate
    if go_simulate:
        msg = (
            "El servicio Go está en modo SIMULATE (sin red WhatsApp). "
            "Para QR auténtico, reiniciá nexus-whatsapp sin NEXUS_WHATSAPP_SIMULATE=1."
        )
        dev_tools = simulate_env or go_simulate
    elif can_real:
        msg = "Motor whatsmeow listo: podés escanear el QR real desde WhatsApp."
        dev_tools = simulate_env
    else:
        msg = "Estado del motor desconocido."
        dev_tools = simulate_env

    return {
        "engine": "go_simulate" if go_simulate else "go_meow",
        "go_configured": True,
        "go_reachable": True,
        "go_simulate": go_simulate,
        "can_pair_real_qr": can_real,
        "dev_simulate_tools": dev_tools,
        "user_message": msg,
        "meow_url": meow_url,
    }


def assert_pairing_engine_allowed() -> dict[str, Any]:
    """Raises ValueError si no hay forma válida de iniciar pairing."""
    status = probe_meow_engine_status()
    if status.get("can_pair_real_qr"):
        return status
    if status.get("dev_simulate_tools"):
        return status
    raise ValueError(str(status.get("user_message") or "Motor WhatsApp no disponible"))
