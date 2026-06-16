from __future__ import annotations

import json

CHANNEL_PRIORITY = ("linkedin", "email", "whatsapp")


def normalize_allowed_channels(channels: list[str] | None) -> list[str]:
    allowed = {"linkedin", "email", "whatsapp"}
    if channels is None:
        return list(CHANNEL_PRIORITY)
    if not channels:
        raise ValueError("Debe habilitarse al menos un canal.")
    seen: set[str] = set()
    raw: list[str] = []
    for c in channels:
        lc = str(c).strip().lower()
        if lc not in allowed:
            raise ValueError(f"Canal inválido: {c}")
        if lc not in seen:
            seen.add(lc)
            raw.append(lc)
    return [ch for ch in CHANNEL_PRIORITY if ch in seen]


def coerce_allowed_channels(raw: object) -> list[str]:
    """Acepta lista JSON (modelo) o TEXT legacy en SQLite."""
    if raw is None:
        return list(CHANNEL_PRIORITY)
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return list(CHANNEL_PRIORITY)
    if isinstance(data, list):
        try:
            return normalize_allowed_channels([str(x).lower().strip() for x in data])
        except ValueError:
            return list(CHANNEL_PRIORITY)
    return list(CHANNEL_PRIORITY)
