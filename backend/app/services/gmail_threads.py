"""Utilidades Gmail API para hilos y mensajes (sin dependencias de inbound/sync)."""

from __future__ import annotations

import urllib.parse

import httpx
from email.utils import parseaddr

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def parse_address_email(header_value: str) -> str:
    _, addr = parseaddr(header_value or "")
    return (addr or "").strip().lower()


def norm_email(s: str | None) -> str:
    return (s or "").strip().lower()


def gmail_get(
    client: httpx.Client, access: str, path: str, *, params: dict | None = None
) -> dict:
    url = f"{GMAIL_API}{path}"
    res = client.get(
        url,
        headers={"Authorization": f"Bearer {access}"},
        params=params or {},
        timeout=45.0,
    )
    if res.status_code == 401:
        return {"_unauthorized": True, "text": res.text}
    res.raise_for_status()
    return res.json()


def fetch_thread_full(client: httpx.Client, access: str, thread_id: str) -> dict | None:
    tid = urllib.parse.quote(thread_id, safe="")
    data = gmail_get(client, access, f"/threads/{tid}", params={"format": "full"})
    if data.get("_unauthorized"):
        return None
    return data


def fetch_message_full(client: httpx.Client, access: str, message_id: str) -> dict | None:
    qmid = urllib.parse.quote(message_id, safe="")
    data = gmail_get(client, access, f"/messages/{qmid}", params={"format": "full"})
    if data.get("_unauthorized"):
        return None
    return data


def fetch_message_metadata(client: httpx.Client, access: str, message_id: str) -> dict | None:
    qmid = urllib.parse.quote(message_id, safe="")
    data = gmail_get(client, access, f"/messages/{qmid}", params={"format": "metadata"})
    if data.get("_unauthorized"):
        return None
    return data


def resolve_thread_id_for_prospect(
    client: httpx.Client,
    access: str,
    *,
    user_email: str,
    prospect_email: str,
) -> str | None:
    """
    Busca el hilo Gmail más reciente entre vendedor y prospecto.
    Incluye hilos donde solo hubo outbound (to:prospect) — típico tras enviar un borrador.
    """
    pe = prospect_email.strip()
    ue = user_email.strip()
    if not pe or not ue:
        return None

    queries = [
        f"from:{pe} newer_than:30d",
        f"from:{pe} to:{ue} newer_than:30d",
        f"to:{pe} from:{ue} newer_than:30d",
        f"to:{pe} newer_than:30d",
    ]
    best_tid: str | None = None
    best_internal = -1
    seen_mids: set[str] = set()

    for q in queries:
        data = gmail_get(client, access, "/messages", params={"q": q, "maxResults": "12"})
        if data.get("_unauthorized"):
            return None
        for ref in data.get("messages") or []:
            mid = ref.get("id")
            if not mid or mid in seen_mids:
                continue
            seen_mids.add(mid)
            meta = fetch_message_metadata(client, access, str(mid))
            if meta is None:
                continue
            tid = (meta.get("threadId") or "").strip()
            internal = int(meta.get("internalDate") or 0)
            if tid and internal >= best_internal:
                best_internal = internal
                best_tid = tid
    return best_tid
