from __future__ import annotations

import json

CHANNEL_PRIORITY = ("linkedin", "email", "whatsapp")

# Canales por defecto: LinkedIn + Email + WhatsApp.
DEFAULT_MVP_CHANNELS = ("linkedin", "email", "whatsapp")


def normalize_allowed_channels(channels: list[str] | None) -> list[str]:
    """Preserva el orden que eligió el usuario (no reordenar por prioridad fija)."""
    allowed = {"linkedin", "email", "whatsapp"}
    if channels is None:
        return list(DEFAULT_MVP_CHANNELS)
    if not channels:
        raise ValueError("Debe habilitarse al menos un canal.")
    seen: set[str] = set()
    raw: list[str] = []
    for c in channels:
        lc = str(c).strip().lower()
        if lc == "call":
            # Retired assisted-call channel; ignore in stored campaign configs.
            continue
        if lc not in allowed:
            raise ValueError(f"Canal inválido: {c}")
        if lc not in seen:
            seen.add(lc)
            raw.append(lc)
    if not raw:
        raise ValueError("Debe habilitarse al menos un canal.")
    return raw


def coerce_allowed_channels(raw: object) -> list[str]:
    """Acepta lista JSON (modelo) o TEXT legacy en SQLite."""
    if raw is None:
        return list(DEFAULT_MVP_CHANNELS)
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return list(DEFAULT_MVP_CHANNELS)
    if isinstance(data, list):
        try:
            return normalize_allowed_channels([str(x).lower().strip() for x in data])
        except ValueError:
            return list(DEFAULT_MVP_CHANNELS)
    return list(DEFAULT_MVP_CHANNELS)
