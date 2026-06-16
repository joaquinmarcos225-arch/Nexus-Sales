"""Constantes de timeout HTTP (sin imports internos — evita ciclos)."""

# Segundos máximos por paso del pipeline (HTTP request completo).
STAGE_TIMEOUT_SEC: dict[str, int] = {
    "companies": 45,
    "extract_companies": 30,
    "prepare_phantom": 30,
    "people": 150,
    "enrich": 45,
    "score": 60,
    "full": 420,
}

STALE_RUN_BUFFER_SEC = 30

WEB_SEARCH_HTTP_TIMEOUT = 25.0
DIRECTORY_FETCH_TIMEOUT = 15.0
PHANTOMBUSTER_HTTP_TIMEOUT = 90.0
PHANTOMBUSTER_POLL_MAX_SEC = 90.0
PHANTOMBUSTER_OUTPUT_FETCH_MAX_SEC = 55.0
PROSPEO_HTTP_TIMEOUT = 8.0
PROSPEO_ENRICH_MAX_SEC = 45
PROSPEO_ENRICH_PER_COMPANY_SEC = 12
PROSPEO_ENRICH_BATCH_SIZE = 3
DOMAIN_RESOLVE_PER_COMPANY_SEC = 4.0
DOMAIN_RESOLVE_MAX_PER_ENRICH = 3
PER_DIRECTORY_SOURCE_TIMEOUT_SEC = 40.0
