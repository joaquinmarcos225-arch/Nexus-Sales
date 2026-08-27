"""Nombres reales para copy de outreach — nunca placeholders tipo Test/Demo/rol."""

from __future__ import annotations

from typing import Any

# Tokens que jamás deben aparecer como saludo o firma en un mensaje al prospecto.
_PLACEHOLDER_TOKENS = frozenset(
    {
        "test",
        "demo",
        "prueba",
        "sample",
        "example",
        "ejemplo",
        "fake",
        "usuario",
        "user",
        "hola",
        "sdr",
        "seller",
        "vendedor",
        "vendedora",
        "director",
        "directora",
        "manager",
        "gerente",
        "admin",
        "owner",
        "nexus",
        "costguard",
    }
)


def is_placeholder_token(value: str | None) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return True
    # "test,", "Test." etc.
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "'"))
    return cleaned in _PLACEHOLDER_TOKENS


def is_placeholder_name(value: str | None) -> bool:
    parts = [p for p in (value or "").strip().split() if p]
    if not parts:
        return True
    return all(is_placeholder_token(p) for p in parts)


def first_real_name_token(full_name: str | None, *, fallback: str = "") -> str:
    """Primer token usable de un nombre completo (salta Test/Demo/roles)."""
    for part in (full_name or "").strip().split():
        if not is_placeholder_token(part):
            return part
    return fallback


def prospect_greeting_name(prospect: dict[str, Any] | str | None) -> str:
    """Nombre para 'Hola X,'. Vacío si no hay nombre real (evitar 'Hola Test,')."""
    if isinstance(prospect, dict):
        name = (prospect.get("name") or "").strip()
    else:
        name = (prospect or "").strip()
    return first_real_name_token(name, fallback="")


def sender_first_name(
    *,
    user: Any | None = None,
    campaign_sender: str | None = None,
    fallback: str = "",
) -> str:
    """
    Remitente para 'Mi nombre es X'.
    Prioridad: first_name del usuario (login) → nombre completo usable → sender de campaña.
    Nunca devuelve Test / Director / Demo.
    """
    if user is not None:
        first = (getattr(user, "first_name", None) or "").strip()
        if first and not is_placeholder_token(first):
            return first
        from_full = first_real_name_token(getattr(user, "name", None), fallback="")
        if from_full:
            return from_full
    from_campaign = first_real_name_token(campaign_sender, fallback="")
    if from_campaign:
        return from_campaign
    return fallback


def scrub_test_tokens_from_text(text: str) -> str:
    """Última red de seguridad: saca tokens placeholder sueltos del copy generado."""
    if not text:
        return text
    out_lines: list[str] = []
    for line in text.splitlines():
        words = line.split()
        kept = [w for w in words if not is_placeholder_token(w.strip(".,;:!?\"'«»"))]
        if kept:
            out_lines.append(" ".join(kept))
        elif line.strip() == "":
            out_lines.append("")
    return "\n".join(out_lines)


# Palabras de entorno demo que no forman parte del nombre comercial real.
_COMPANY_NOISE_TOKENS = frozenset(
    {
        "demo",
        "test",
        "prueba",
        "sample",
        "example",
        "ejemplo",
        "client",
        "cliente",
        "sandbox",
        "staging",
        "dev",
        "qa",
    }
)


# Etiquetas genéricas que jamás deben ir al copy como si fueran una empresa real.
_GENERIC_COMPANY_LABELS = frozenset(
    {
        "empresa",
        "company",
        "compania",
        "compañía",
        "the company",
        "unknown",
        "desconocida",
        "n/a",
        "na",
        "-",
        "—",
    }
)


def prospect_company_display(raw_name: str | None) -> str:
    """Empresa del prospecto para mensajes. Vacío si es placeholder («Empresa»)."""
    name = outreach_company_display(raw_name)
    if not name:
        return ""
    key = " ".join(
        "".join(ch for ch in name.lower() if ch.isalnum() or ch in ("-", "'", " ")).split()
    )
    if not key or key in _GENERIC_COMPANY_LABELS:
        return ""
    return name


_PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.com.ar",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "msn.com",
        "aol.com",
    }
)
_COMPOUND_TLDS = frozenset(
    {
        "com.ar",
        "com.br",
        "com.mx",
        "com.co",
        "com.pe",
        "com.cl",
        "com.uy",
        "com.ec",
        "co.uk",
        "co.jp",
        "com.au",
    }
)


def company_name_from_domain(domain: str | None) -> str:
    """'juan@acme.com.ar' / 'www.acme.com' → 'Acme'. Vacío si es Gmail u otro personal."""
    raw = (domain or "").strip().lower()
    if not raw:
        return ""
    if "@" in raw and "://" not in raw:
        raw = raw.split("@", 1)[-1]
    raw = raw.replace("https://", "").replace("http://", "")
    raw = raw.split("/")[0].split("?")[0].split(":")[0]
    raw = raw.removeprefix("www.").strip(".")
    if not raw or raw in _PERSONAL_EMAIL_DOMAINS:
        return ""
    parts = [p for p in raw.split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _COMPOUND_TLDS:
        core = parts[-3]
    elif len(parts) >= 2:
        core = parts[-2]
    else:
        core = parts[0]
    if core in {"www", "mail", "email", "smtp", "webmail"}:
        return ""
    pretty = core.replace("-", " ").replace("_", " ").strip()
    if not pretty or pretty in _GENERIC_COMPANY_LABELS:
        return ""
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in pretty.split())


def resolve_prospect_company_name(
    *,
    company_name: str | None = None,
    email: str | None = None,
    website: str | None = None,
    domain: str | None = None,
) -> str:
    """Nombre usable: Prospeo/org → web → dominio del mail corporativo."""
    real = prospect_company_display(company_name)
    if real:
        return real
    for source in (domain, website, email):
        guessed = company_name_from_domain(source)
        if guessed:
            return guessed
    return ""


def scrub_generic_empresa_in_copy(
    text: str,
    *,
    prospect_company: str | None = None,
    brand: str | None = None,
) -> str:
    """Reemplaza [Empresa] / Empresa suelta del playbook por el nombre real o un giro neutro."""
    import re

    if not text:
        return text
    real = prospect_company_display(prospect_company)
    brand_clean = outreach_company_display(brand)
    if real:
        return re.sub(r"\[Empresa\]|\{Empresa\}", real, text)

    out = re.sub(r"\[Empresa\]|\{Empresa\}", "tu equipo", text)
    out = re.sub(
        r"(?<![Tt]u )(?<![Nn]uestra )(?<![Nn]uestro )\bEmpresa\b",
        "tu equipo",
        out,
    )
    if brand_clean:
        out = out.replace("de tu equipo.", f"de {brand_clean}.")
        out = out.replace("de tu equipo,", f"de {brand_clean},")
    return out


def outreach_company_display(raw_name: str | None) -> str:
    """
    Nombre de empresa para mensajes salientes.

    - Toma Company.name del tenant.
    - Quita ruido de seed/demo («CostGuard Demo Client» → «CostGuard»).
    - No trata marcas reales (CostGuard, Nexus, etc.) como placeholder de persona.
    - Si queda vacío (solo Demo/Test), no inventa marca.
    """
    parts = [p for p in (raw_name or "").strip().split() if p]
    if not parts:
        return ""
    kept: list[str] = []
    for part in parts:
        token = "".join(ch for ch in part.lower() if ch.isalnum() or ch in ("-", "'"))
        if token in _COMPANY_NOISE_TOKENS:
            continue
        kept.append(part)
    if kept:
        return " ".join(kept)
    return ""
