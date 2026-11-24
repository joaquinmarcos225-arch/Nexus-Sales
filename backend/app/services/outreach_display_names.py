"""Nombres reales para copy de outreach — nunca placeholders tipo Test/Demo/rol."""

from __future__ import annotations

import re
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


# Roles / genéricos: no saludar con esto. «Test»/«Demo» sí pueden ser el nombre
# de pila en QA («Test 2» → Hola Test, no Hola 2).
_GREETING_SKIP_TOKENS = frozenset(
    {
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
        "usuario",
        "user",
    }
)


def _token_alnum(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "'"))


def first_real_name_token(full_name: str | None, *, fallback: str = "") -> str:
    """
    Nombre de pila para saludo: primer token del nombre (no el apellido).
    - «Test 2» → Test (no 2)
    - «María López» → María
    - Solo «Test» / solo dígitos / solo rol → vacío
    """
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return fallback
    # Nombre entero placeholder (solo «Test» / «Demo») → no saludar con eso.
    if is_placeholder_name(full_name):
        return fallback

    def _usable(part: str) -> bool:
        cleaned = _token_alnum(part)
        if not cleaned or cleaned.isdigit():
            return False
        if cleaned in _GREETING_SKIP_TOKENS:
            return False
        return True

    # Preferí el primer token si sirve (nombre, no apellido ni número).
    if _usable(parts[0]):
        return parts[0]
    for part in parts[1:]:
        if _usable(part):
            return part
    return fallback


def prospect_greeting_name(prospect: dict[str, Any] | str | None) -> str:
    """Nombre para 'Hola X,'. Vacío si no hay nombre de pila usable."""
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


def is_placeholder_company_name(raw_name: str | None) -> bool:
    """True si no hay identidad empresarial confirmada (vacío, —, Empresa, etc.)."""
    return not prospect_company_display(raw_name)


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


# Nombres frecuentes en LATAM para inferir género del saludo/rol (heurística, no exhaustiva).
_FEMININE_FIRST_NAMES = frozenset(
    {
        "maría",
        "maria",
        "ana",
        "lucía",
        "lucia",
        "valentina",
        "camila",
        "florencia",
        "paula",
        "carolina",
        "fernanda",
        "gabriela",
        "daniela",
        "natalia",
        "verónica",
        "veronica",
        "silvia",
        "andrea",
        "patricia",
        "claudia",
        "lorena",
        "vanessa",
        "victoria",
        "soledad",
        "romina",
        "juliana",
        "martina",
        "belén",
        "belen",
        "laura",
        "cecilia",
        "mariana",
        "adriana",
        "elena",
        "rosa",
        "sandra",
        "monica",
        "mónica",
        "alejandra",
        "guadalupe",
        "josefina",
        "agustina",
        "lourdes",
        "mercedes",
        "liliana",
        "griselda",
        "ivana",
        "miriam",
        "noelia",
        "romina",
        "silvina",
        "yolanda",
    }
)
_MASCULINE_FIRST_NAMES = frozenset(
    {
        "juan",
        "jose",
        "josé",
        "carlos",
        "martin",
        "martín",
        "diego",
        "pablo",
        "facundo",
        "german",
        "germán",
        "matias",
        "matías",
        "nicolas",
        "nicolás",
        "rodrigo",
        "sebastian",
        "sebastián",
        "gustavo",
        "fernando",
        "marcos",
        "lucas",
        "jorge",
        "miguel",
        "ricardo",
        "alejandro",
        "gabriel",
        "daniel",
        "eduardo",
        "francisco",
        "guillermo",
        "hernan",
        "hernán",
        "joaquin",
        "joaquín",
        "leonardo",
        "manuel",
        "mario",
        "oscar",
        "óscar",
        "pedro",
        "rafael",
        "sergio",
        "tomas",
        "tomás",
        "victor",
        "víctor",
    }
)
_AMBIGUOUS_A_ENDING_NAMES = frozenset({"andrea", "nicola", "joshua", "luca", "ema"})

# Sustituciones explícitas de rol masculino → femenino (case-aware).
_ROLE_FEMINIZE: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bdirector\b", re.I), "director", "directora"),
    (re.compile(r"\bvendedor\b", re.I), "vendedor", "vendedora"),
    (re.compile(r"\bjefe\b", re.I), "jefe", "jefa"),
    (re.compile(r"\bencargado\b", re.I), "encargado", "encargada"),
    (re.compile(r"\bpropietario\b", re.I), "propietario", "propietaria"),
    # «Gerente» se usa igual para mujeres; no convertir a «Gerenta».
    (re.compile(r"\bcoordinador\b", re.I), "coordinador", "coordinadora"),
    (re.compile(r"\badministrador\b", re.I), "administrador", "administradora"),
    (re.compile(r"\basistente\b", re.I), "asistente", "asistente"),  # invariable
    (re.compile(r"\bcomercial\b", re.I), "comercial", "comercial"),  # invariable
)


def infer_prospect_gender(prospect_name: str | None) -> str | None:
    """'f' | 'm' | None — heurística por primer nombre."""
    first = first_real_name_token(prospect_name, fallback="")
    if not first:
        return None
    low = first.lower()
    if low in _FEMININE_FIRST_NAMES:
        return "f"
    if low in _MASCULINE_FIRST_NAMES:
        return "m"
    if low in _AMBIGUOUS_A_ENDING_NAMES:
        return None
    if low.endswith("a") and len(low) >= 4:
        return "f"
    return None


def _preserve_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        return replacement.capitalize()
    return replacement


def feminize_spanish_role(role: str) -> str:
    """Adapta títulos masculinos a femenino cuando el prospecto es mujer."""
    text = (role or "").strip()
    if not text:
        return ""
    if re.search(r"(ora|esa|iza|triz|anta|ista)\b", text, re.I):
        return text
    out = text
    for pattern, masc, fem in _ROLE_FEMINIZE:
        if fem == masc:
            continue

        def _repl(m: re.Match[str]) -> str:
            return _preserve_case(m.group(0), fem)

        out = pattern.sub(_repl, out)
    return out


def company_label_for_outreach(name: str | None) -> str:
    """
    Una etiqueta para el copy: o el nombre o la sigla, no las dos.
    «ISS - Intelligent Security Systems» → «Intelligent Security Systems».
    """
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if not raw:
        return ""
    m = re.match(r"^([A-ZÁÉÍÓÚÑ0-9]{2,8})\s*[-–—|:]\s+(.+)$", raw)
    if m:
        acr, rest = m.group(1), m.group(2).strip()
        if len(rest.split()) >= 2 and len(rest) > len(acr):
            return rest
    m2 = re.match(r"^(.+?)\s+([A-ZÁÉÍÓÚÑ]{2,8})$", raw)
    if m2:
        left, acr = m2.group(1).strip(), m2.group(2)
        initials = "".join(w[0] for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", left))
        if initials.upper() == acr.upper() and len(left.split()) >= 2:
            return left
    return raw


def _company_tokens_for_role(company: str | None) -> set[str]:
    tokens: set[str] = set()
    for blob in (company, company_label_for_outreach(company)):
        raw = re.sub(r"\s+", " ", (blob or "").strip())
        if not raw:
            continue
        for tok in re.split(r"[^\wÁÉÍÓÚÑáéíóúñ]+", raw):
            if len(tok) >= 3 or (tok.isupper() and len(tok) >= 2):
                tokens.add(tok.lower())
    return tokens


# Cargos junior / de práctica: no afirmarlos en el primer mensaje.
# Lista corta: no incluye assistant/junior ni «Student Success».
_JUNIOR_ROLE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:intern|internship|pasante|pasant[ií]a|trainee|becari[oa]|practicante|apprentice)\b"
    r"|"
    r"\b(?:estudiante|student)(?!\s+success)\b"
    r")"
)


def _is_junior_outreach_role(role: str | None) -> bool:
    return bool(_JUNIOR_ROLE_RE.search((role or "").strip()))


def _strip_company_from_role(role: str, company: str | None) -> str:
    tokens = _company_tokens_for_role(company)
    if not tokens or not role:
        return role
    kept = [
        w
        for w in role.split()
        if re.sub(r"[^\wÁÉÍÓÚÑáéíóúñ]", "", w).lower() not in tokens
    ]
    out = " ".join(kept).strip(" ,.-")
    return out or role


def _clean_role_headline(role: str, company: str | None = None) -> str:
    """
    LinkedIn suele pegar 'Title | keywords | …'. Para copy usamos solo el título corto.
    Si es headline inglés largo/ambiguo, preferimos no nombrarlo (mejor «por tu rol»).
    """
    text = (role or "").strip()
    if not text:
        return ""
    # Primera sección antes de pipes / bullets de SEO LinkedIn.
    text = re.split(r"\s*[|•·]\s*", text, maxsplit=1)[0].strip()
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    text = _strip_company_from_role(text, company)
    if not text:
        return ""
    # Headline demasiado largo → no forzar en el mensaje.
    if len(text) > 56:
        return ""
    low = text.lower()
    # Inglés típico de LinkedIn: mapear corto o omitir (nunca feminizar «Director» en inglés crudo).
    english_markers = (
        "business development",
        "commercial",
        "sales",
        "marketing",
        "head of",
        "vp ",
        "chief",
        "manager",
        "&",
    )
    looks_english = any(m in low for m in english_markers) or bool(
        re.search(r"\b(director|manager|officer|lead)\b", low)
        and not re.search(
            r"\b(director(?:a)?|gerente|jef[ea]|vendedor(?:a)?|l[ií]der)\s+(de|comercial|ventas)",
            low,
        )
        and re.search(r"[a-z]", low)
        and not re.search(r"[áéíóúñ]", low)
    )
    # Título claramente en español (con género o preposición ES).
    looks_spanish = bool(
        re.search(
            r"\b(directora|gerente|gerenta|jefa|vendedora|líder|lider)\b|"
            r"\bdirector\s+(de|comercial|general|de\s+ventas)\b",
            low,
        )
    )
    if looks_english and not looks_spanish:
        if "business development" in low and "director" in low:
            return "Director de desarrollo de negocios"
        if re.search(r"\bcommercial\b.*\bdirector\b|\bdirector\b.*\bcommercial\b", low):
            return "Director comercial"
        if "sales" in low and "marketing" in low and "director" in low:
            return "Director de ventas y marketing"
        if "sales" in low and "director" in low:
            return "Director de ventas"
        if re.search(r"\bregional\s+sales\s+manager\b", low):
            return "Gerente comercial regional"
        if re.search(r"\bsales\s+manager\b", low):
            return "Gerente comercial"
        # Título corto en inglés («Head of Sales», «CEO & Founder»): se usa tal cual; omitirlo
        # deja la frase sin rol aunque lo tengamos.
        out = re.sub(r"\s*&\s*", " y ", text)
        out = re.sub(r"(?i)\bco-?founder\b", "Cofundador", out)
        out = re.sub(r"(?i)\bfounder\b", "Fundador", out)
        return out.strip()
    return text


def prospect_role_for_outreach(
    role: str | None,
    prospect_name: str | None,
    company_name: str | None = None,
) -> str:
    """Rol listo para copy; vacío si el headline no es usable."""
    raw = role or ""
    if _is_junior_outreach_role(raw):
        return ""
    cleaned = _clean_role_headline(raw, company=company_name)
    if not cleaned or _is_junior_outreach_role(cleaned):
        return ""
    if infer_prospect_gender(prospect_name) == "f":
        return feminize_spanish_role(cleaned)
    return cleaned


def _email_domain_host(email: str | None) -> str:
    raw = (email or "").strip().lower()
    if "@" not in raw:
        return ""
    dom = raw.split("@", 1)[-1].strip()
    if not dom or dom in _PERSONAL_EMAIL_DOMAINS:
        return ""
    return dom


def _website_domain_host(website: str | None) -> str:
    from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

    return _website_domain(website) or ""


def prospect_company_for_outreach(
    *,
    company_name: str | None = None,
    email: str | None = None,
    website: str | None = None,
    source_provider: str | None = None,
) -> str:
    """
    Empresa del prospecto para el copy: se nombra siempre que la tengamos.

    Cargados a mano (`source_provider=manual`): tal cual la escribió el SDR.
    El resto: sin ruido de seed/demo ni etiquetas genéricas («Empresa», «—»).
    """
    del email, website
    if (source_provider or "").strip().lower() == "manual":
        typed = " ".join((company_name or "").split())
        if typed.lower() in _GENERIC_COMPANY_LABELS:
            return ""
        return typed
    return prospect_company_display(company_name)


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


def product_outreach_display_name(raw_name: str | None) -> str:
    """
    Nombre corto del producto para copy outbound.

    La ficha puede traer «Contractia | Software de contratos…»; en el mensaje
    usamos solo el nombre comercial (antes de | / • / ·).
    """
    text = re.sub(r"\s+", " ", (raw_name or "").strip())
    if not text:
        return ""
    head = re.split(r"\s*[|•·]\s*", text, maxsplit=1)[0].strip()
    # Sufijo largo tipo «Nombre - descripción…»
    if " - " in head and len(head) > 36:
        head = head.split(" - ", 1)[0].strip()
    head = head.strip(" ,.-")
    if len(head) > 48:
        head = head[:45].rstrip() + "…"
    return head
