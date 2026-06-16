"""Diagnóstico de carga de APOLLO_API_KEY (sin exponer el secreto completo)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ENV_VAR_NAME = "APOLLO_API_KEY"
# Misma ruta que app/main.py: backend/app/main.py → backend/.env
ENV_FILE_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".env"


def _key_len(raw: str | None) -> int:
    return len((raw or "").strip())


def diagnose_apollo_env(*, reload_dotenv: bool = False) -> dict:
    """
    Inspecciona por qué Apollo aparece configurado o no.
    No incluye la API key completa.
    """
    in_environ_before = ENV_VAR_NAME in os.environ
    raw_before = os.getenv(ENV_VAR_NAME)
    len_before = _key_len(raw_before)

    file_exists = ENV_FILE_PATH.is_file()
    file_values: dict[str, str | None] = {}
    file_key_len = 0
    file_key_prefix = ""
    if file_exists:
        try:
            file_values = dotenv_values(ENV_FILE_PATH)
            file_raw = file_values.get(ENV_VAR_NAME)
            file_key_len = _key_len(file_raw)
            stripped = (file_raw or "").strip()
            file_key_prefix = stripped[:4] + "…" if len(stripped) >= 4 else ""
        except Exception as e:
            file_values = {"_error": str(e)}

    dotenv_loaded = False
    if reload_dotenv:
        dotenv_loaded = bool(load_dotenv(ENV_FILE_PATH, override=True))

    raw_after = os.getenv(ENV_VAR_NAME)
    len_after = _key_len(raw_after)
    configured = len_after > 0

    reasons: list[str] = []
    if not file_exists:
        reasons.append(f"No existe el archivo esperado: {ENV_FILE_PATH}")
    elif file_key_len == 0:
        reasons.append(
            f"{ENV_VAR_NAME} ausente o vacía dentro de {ENV_FILE_PATH.name} "
            "(revisá typo, espacios o línea comentada con #)."
        )
    elif len_before == 0 and file_key_len > 0 and in_environ_before:
        reasons.append(
            "La variable ya existía en el entorno del proceso con valor vacío; "
            "load_dotenv(override=False) no la reemplazó. Solución: reiniciar uvicorn "
            "después de quitar APOLLO_API_KEY del entorno, o usar override=True en main.py."
        )
    elif len_before == 0 and file_key_len > 0 and not reload_dotenv:
        reasons.append(
            "El .env tiene valor pero os.environ aún está vacío: "
            "load_dotenv no corrió en este proceso o corrió antes de escribir el .env."
        )
    elif configured:
        reasons.append("APOLLO_API_KEY presente en os.environ con longitud > 0.")
    else:
        reasons.append("APOLLO_API_KEY no detectada en os.environ tras la inspección.")

    return {
        "env_var_name": ENV_VAR_NAME,
        "env_file_path": str(ENV_FILE_PATH),
        "env_file_exists": file_exists,
        "env_file_key_length": file_key_len,
        "env_file_key_prefix": file_key_prefix,
        "in_os_environ_before_check": in_environ_before,
        "os_environ_length_before": len_before,
        "os_environ_length_after": len_after,
        "os_environ_key_prefix": ((raw_after or "").strip()[:4] + "…")
        if len_after >= 4
        else "",
        "dotenv_reload_attempted": reload_dotenv,
        "dotenv_reload_returned": dotenv_loaded,
        "configured": configured,
        "reason": " | ".join(reasons),
        "main_py_load_dotenv_line": "backend/app/main.py -> load_dotenv(backend/.env, override=True)",
        "reader_module": "apollo_client.is_configured -> os.getenv('APOLLO_API_KEY')",
    }
