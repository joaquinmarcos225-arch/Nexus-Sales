"""Carga y lectura de variables de entorno para Lead Sourcing."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env (misma ruta que app/main.py)
ENV_FILE_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".env"


def refresh_lead_sourcing_env() -> bool:
    """Relee backend/.env antes de evaluar proveedores (status endpoint)."""
    if not ENV_FILE_PATH.is_file():
        return False
    return bool(load_dotenv(ENV_FILE_PATH, override=True))


def getenv(key: str) -> str:
    """Valor limpio (sin comillas ni espacios)."""
    raw = os.getenv(key)
    if raw is None:
        return ""
    return raw.strip().strip('"').strip("'")


def env_present(key: str) -> bool:
    return bool(getenv(key))
