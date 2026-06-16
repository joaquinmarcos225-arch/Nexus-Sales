ICP_MISSING_MESSAGE = (
    "Completá al menos un parámetro del ICP para que Nexus pueda prospectar con criterio."
)


def _normalized(value: str | None) -> str:
    return (value or "").strip().lower()


def is_icp_token_empty(value: str | None) -> bool:
    v = _normalized(value)
    if v == "":
        return True
    return v in {
        "no importante",
        "no importa",
        "sin preferencia",
        "cualquiera",
        "-",
        "--",
        "n/a",
        "na",
    }


def icp_has_signal(*fields: str | None) -> bool:
    return any(not is_icp_token_empty(f) for f in fields)


def assert_icp_has_signal(
    *,
    target_company_size: str | None,
    target_industry: str | None,
    target_country: str | None,
    target_language: str | None,
    target_role: str | None,
) -> None:
    if not icp_has_signal(
        target_company_size,
        target_industry,
        target_country,
        target_language,
        target_role,
    ):
        raise ValueError(ICP_MISSING_MESSAGE)


def normalize_optional_icp_field(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    if is_icp_token_empty(text):
        return None
    return text
