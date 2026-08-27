"""Constantes de timeout HTTP (sin imports internos — evita ciclos)."""

# Segundos máximos por paso del pipeline (HTTP request completo).
STAGE_TIMEOUT_SEC: dict[str, int] = {
    "companies": 90,
    # Debe cubrir PROSPEO_ENRICH_MAX_SEC (+ margen). Antes 45s mataba el lote entero.
    "enrich": 210,
    "score": 60,
    "full": 420,
}

STALE_RUN_BUFFER_SEC = 30

WEB_SEARCH_HTTP_TIMEOUT = 25.0
DIRECTORY_FETCH_TIMEOUT = 15.0
PROSPEO_HTTP_TIMEOUT = 8.0
PROSPEO_ENRICH_MAX_SEC = 180
PROSPEO_ENRICH_PER_COMPANY_SEC = 25
PROSPEO_ENRICH_BATCH_SIZE = 8
# Throttle entre búsquedas: equilibrio rate-limit vs velocidad de cupo.
PROSPEO_SEARCH_THROTTLE_SEC = 0.25
DOMAIN_RESOLVE_PER_COMPANY_SEC = 4.0
DOMAIN_RESOLVE_MAX_PER_ENRICH = 8
PER_DIRECTORY_SOURCE_TIMEOUT_SEC = 40.0
