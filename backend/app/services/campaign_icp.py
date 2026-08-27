ICP_MISSING_MESSAGE = (
    "Completá al menos un parámetro del ICP para que Nexus pueda prospectar con criterio."
)

ICP_B2C_MISSING_MESSAGE = (
    "ICP B2C: completá región y quién buscamos o keywords (LinkedIn)."
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
    target_company_size: str | None = None,
    target_industry: str | None = None,
    target_country: str | None = None,
    target_language: str | None = None,
    target_role: str | None = None,
    target_area: str | None = None,
    target_interests: str | None = None,
    outreach_mode: str | None = None,
) -> None:
    mode = (outreach_mode or "b2b").strip().lower()
    if mode == "b2c":
        # Región + (quién o keywords). Idioma/situación no alcanzan solos.
        has_region = not is_icp_token_empty(target_country)
        has_who_or_kw = icp_has_signal(target_role, target_interests)
        if not (has_region and has_who_or_kw):
            raise ValueError(ICP_B2C_MISSING_MESSAGE)
        return
    if not icp_has_signal(
        target_company_size,
        target_industry,
        target_country,
        target_language,
        target_role,
        target_area,
        target_interests,
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
