"""Cliente Apollo.io — búsqueda y enrichment de datos reales."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

APOLLO_BASE = "https://api.apollo.io/api/v1"
DEFAULT_TIMEOUT = 45.0


class ApolloNotConfiguredError(RuntimeError):
    pass


class ApolloAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    key = (os.getenv("APOLLO_API_KEY") or "").strip()
    configured = bool(key)
    logger.debug(
        "[apollo] is_configured=%s APOLLO_API_KEY length=%s",
        configured,
        len(key),
    )
    return configured


def _api_key() -> str:
    key = (os.getenv("APOLLO_API_KEY") or "").strip()
    if not key:
        raise ApolloNotConfiguredError(
            "Apollo no está configurado. Agregá APOLLO_API_KEY en backend/.env "
            "(master API key desde Apollo → Settings → Integrations)."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "accept": "application/json",
        "x-api-key": _api_key(),
    }


def _request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
    url = f"{APOLLO_BASE}{path}"
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.request(
                method,
                url,
                headers=_headers(),
                params=params,
                json=json_body,
            )
    except httpx.RequestError as e:
        raise ApolloAPIError(f"No se pudo conectar con Apollo: {e}") from e

    if resp.status_code == 401:
        raise ApolloAPIError("API key de Apollo inválida o sin permisos.", status_code=401)
    if resp.status_code == 403:
        raise ApolloAPIError(
            "Apollo rechazó la operación (403). Verificá que la key sea master y tenga acceso a People Search.",
            status_code=403,
        )
    if resp.status_code >= 400:
        detail = resp.text[:400] if resp.text else resp.reason_phrase
        raise ApolloAPIError(f"Apollo error {resp.status_code}: {detail}", status_code=resp.status_code)

    try:
        return resp.json()
    except Exception as e:
        raise ApolloAPIError("Respuesta inválida de Apollo.") from e


def search_people(*, filters: dict[str, Any], page: int = 1, per_page: int = 25) -> dict:
    params = {**filters, "page": page, "per_page": per_page}
    return _request("POST", "/mixed_people/api_search", params=params)


def search_companies(*, filters: dict[str, Any], page: int = 1, per_page: int = 25) -> dict:
    params = {**filters, "page": page, "per_page": per_page}
    return _request("POST", "/mixed_companies/search", params=params)


def bulk_enrich_people(
    apollo_ids: list[str],
    *,
    reveal_phone: bool = False,
) -> list[dict]:
    """Enriquece hasta 10 personas por request (lotes)."""
    if not apollo_ids:
        return []
    enriched: list[dict] = []
    chunk_size = 10
    for i in range(0, len(apollo_ids), chunk_size):
        chunk = apollo_ids[i : i + chunk_size]
        body = {"details": [{"id": pid} for pid in chunk]}
        params = {
            "reveal_personal_emails": "false",
            "reveal_phone_number": "true" if reveal_phone else "false",
        }
        data = _request("POST", "/people/bulk_match", params=params, json_body=body)
        matches = data.get("matches") or data.get("people") or []
        if isinstance(matches, list):
            enriched.extend([m for m in matches if isinstance(m, dict)])
    return enriched
