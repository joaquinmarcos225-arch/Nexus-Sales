"""Idioma de outreach: normaliza campaña → es|en e instrucciones de prompt."""

from __future__ import annotations

import re
from typing import Any, Literal

OutreachLang = Literal["es", "en"]

_ES_HINTS = (
    "es",
    "esp",
    "español",
    "espanol",
    "spanish",
    "castellano",
    "rioplatense",
)
_EN_HINTS = (
    "en",
    "eng",
    "english",
    "inglés",
    "ingles",
)


def normalize_outreach_language(raw: str | None) -> OutreachLang:
    """
    Mapea target_language libre (form) a es|en.
    Vacío / No importante / otros → es (default producto).
    """
    text = " ".join(str(raw or "").strip().lower().split())
    if not text or text in ("no importante", "n/a", "na", "-", "any", "cualquiera"):
        return "es"
    # Prefer explicit English before Spanish (bilingual strings often list both).
    for hint in _EN_HINTS:
        if hint in text:
            # "español e inglés" → still treat as bilingual; v1 picks English if both present
            # unless Spanish is the only clear signal without English.
            pass
    has_en = any(h in text for h in _EN_HINTS)
    has_es = any(h in text for h in _ES_HINTS)
    if has_en and not has_es:
        return "en"
    if has_en and has_es:
        # "Español / Inglés" → default Spanish (LATAM product); explicit "Inglés" alone → en
        if text.startswith("ing") or text.startswith("en ") or text in ("en", "eng", "english"):
            return "en"
        return "es"
    if has_es:
        return "es"
    return "es"


def language_from_campaign_dict(campaign: dict[str, Any] | None) -> OutreachLang:
    if not isinstance(campaign, dict):
        return "es"
    raw = campaign.get("outreach_language") or campaign.get("target_language")
    return normalize_outreach_language(str(raw) if raw is not None else None)


SellerDialect = Literal["rioplatense", "es_latam", "es_es", "en"]

_COUNTRY_EN = ("estados unidos", "united states", "usa", "us", "uk", "inglaterra", "reino unido", "united kingdom")
_COUNTRY_ES_ES = ("españa", "espana", "spain")
_COUNTRY_RIOPLA = ("argentina", "uruguay", "rioplatense", "buenos aires")


def dialect_from_country(country: str | None) -> SellerDialect:
    """País de Mi estilo → variante. Default producto: rioplatense (Argentina)."""
    raw = " ".join(str(country or "").strip().lower().split())
    folded = (
        raw.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    if not folded:
        return "rioplatense"
    tokens = set(folded.replace("-", " ").split())
    if folded in _COUNTRY_EN or tokens & {"usa", "uk"} or "estados unidos" in folded or "united states" in folded:
        return "en"
    if any(h in folded for h in _COUNTRY_ES_ES):
        return "es_es"
    if any(h in folded for h in _COUNTRY_RIOPLA):
        return "rioplatense"
    return "es_latam"


def dialect_prompt_block(dialect: SellerDialect | str | None) -> str:
    code = dialect if dialect in ("rioplatense", "es_latam", "es_es", "en") else dialect_from_country(
        str(dialect) if dialect else None
    )
    if code == "en":
        return (
            "\n\nRESPONSE LANGUAGE (MANDATORY): Write the ENTIRE message in natural business English. "
            "Do not use Spanish. Keep the same commercial intent and structure.\n"
        )
    if code == "es_es":
        return (
            "\n\nIDIOMA (OBLIGATORIO): español de España. Tuteo (tú / tienes / puedes / pásame / aquí). "
            "NUNCA voseo argentino (pasás, tenés, preferís, vos, che, acá). "
            "Trato de tú, cercano, B2B. Vale / perfecto en vez de dale/joya.\n"
        )
    if code == "es_latam":
        return (
            "\n\nIDIOMA (OBLIGATORIO): español latinoamericano con tuteo (tú / tienes / puedes). "
            "NUNCA voseo argentino (pasás, tenés, preferís, vos). Sin 'che'.\n"
        )
    return (
        "\n\nIDIOMA DE RESPUESTA (OBLIGATORIO): Escribí TODO el mensaje en español natural "
        "(neutro-argentino / rioplatense suave). No uses inglés salvo nombres propios.\n"
    )


_VOSEO_TO_TUTEO: tuple[tuple[str, str], ...] = (
    (r"\bpasás\b", "pasas"),
    (r"\bpreferís\b", "prefieres"),
    (r"\btenés\b", "tienes"),
    (r"\bpodés\b", "puedes"),
    (r"\bquedás\b", "quedas"),
    (r"\bdecime\b", "dime"),
    (r"\bpasame\b", "pásame"),
    (r"\bmandame\b", "mándame"),
    (r"\bavisame\b", "avísame"),
    (r"\bescribime\b", "escríbeme"),
    (r"\bcontame\b", "cuéntame"),
    (r"\bacá\b", "aquí"),
    (r"\bvos\b", "tú"),
)


def localize_sdr_copy(text: str | None, *, country: str | None = None, dialect: str | None = None) -> str:
    """Ajusta voseo de plantillas al español del SDR (Mi estilo)."""
    raw = text or ""
    code = dialect if dialect in ("rioplatense", "es_latam", "es_es", "en") else dialect_from_country(country)
    if code in ("rioplatense", "en") or not raw:
        return raw
    out = raw
    for pat, repl in _VOSEO_TO_TUTEO:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def language_prompt_block(
    lang: OutreachLang | str | None,
    *,
    country: str | None = None,
) -> str:
    code: OutreachLang = lang if lang in ("es", "en") else normalize_outreach_language(
        str(lang) if lang else None
    )
    dialect = dialect_from_country(country)
    if code == "en" or dialect == "en":
        return dialect_prompt_block("en")
    if country:
        return dialect_prompt_block(dialect)
    return dialect_prompt_block("rioplatense")


def classify_language_prompt_block(lang: OutreachLang | str | None) -> str:
    code: OutreachLang = lang if lang in ("es", "en") else normalize_outreach_language(
        str(lang) if lang else None
    )
    if code == "en":
        return (
            "\nThe inbound message may be in English. Classify intent correctly regardless of language. "
            "JSON keys stay the same. Relative timing phrases like 'next week' / 'in 2 days' must set "
            "defer_resume_at when clear.\n"
        )
    return (
        "\nEl mensaje puede estar en español o inglés. Clasificá la intención sin importar el idioma. "
        "Las claves JSON no cambian.\n"
    )


def adapt_outbound_text_to_language(
    *,
    body: str,
    subject: str | None,
    channel: str,
    lang: OutreachLang,
) -> tuple[str | None, str]:
    """Si lang=en, reescribe el borrador del banco ES a inglés vía OpenAI. Si falla, deja ES."""
    if lang != "en":
        return subject, body
    text = (body or "").strip()
    if not text:
        return subject, body
    try:
        from app.services import openai_service as oai

        if not oai.openai_configured():
            return subject, body
        ch = (channel or "email").lower()
        sys = (
            "You adapt B2B sales outreach from Spanish to natural business English. "
            "Keep meaning, length, and CTA. No markdown. No new pitch. Plain text only."
        )
        user = (
            f"Channel: {ch}\n"
            + (f"Subject (Spanish): {subject}\n" if subject else "")
            + f"Body (Spanish):\n{text}\n\n"
            + (
                'Return JSON: {"subject":"...","body":"..."}'
                if ch == "email"
                else 'Return JSON: {"body":"..."}'
            )
        )
        raw = oai._raw_chat(sys, user, temperature=0.35, max_output_tokens=320)
        cleaned = oai._strip_json_fence(raw)
        import json

        data = json.loads(cleaned)
        if not isinstance(data, dict):
            return subject, body
        new_body = str(data.get("body") or "").strip() or text
        new_subj = subject
        if ch == "email":
            new_subj = str(data.get("subject") or subject or "").strip() or subject
        return new_subj, new_body
    except Exception:
        return subject, body


def language_from_campaign_model(campaign: Any | None) -> OutreachLang:
    if campaign is None:
        return "es"
    return normalize_outreach_language(getattr(campaign, "target_language", None))
