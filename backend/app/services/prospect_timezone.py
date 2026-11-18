"""Zona horaria local del prospecto (país/región o prefijo telefónico) para la ventana de envío."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_COUNTRY_ZONES: dict[str, str] = {
    "argentina": "America/Argentina/Buenos_Aires",
    "ar": "America/Argentina/Buenos_Aires",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "caba": "America/Argentina/Buenos_Aires",
    "chile": "America/Santiago",
    "cl": "America/Santiago",
    "santiago de chile": "America/Santiago",
    "mexico": "America/Mexico_City",
    "mx": "America/Mexico_City",
    "cdmx": "America/Mexico_City",
    "ciudad de mexico": "America/Mexico_City",
    "mexico city": "America/Mexico_City",
    "monterrey": "America/Monterrey",
    "guadalajara": "America/Mexico_City",
    "colombia": "America/Bogota",
    "co": "America/Bogota",
    "bogota": "America/Bogota",
    "medellin": "America/Bogota",
    "peru": "America/Lima",
    "pe": "America/Lima",
    "lima": "America/Lima",
    "uruguay": "America/Montevideo",
    "uy": "America/Montevideo",
    "montevideo": "America/Montevideo",
    "ecuador": "America/Guayaquil",
    "ec": "America/Guayaquil",
    "quito": "America/Guayaquil",
    "guayaquil": "America/Guayaquil",
    "paraguay": "America/Asuncion",
    "py": "America/Asuncion",
    "bolivia": "America/La_Paz",
    "bo": "America/La_Paz",
    "venezuela": "America/Caracas",
    "ve": "America/Caracas",
    "costa rica": "America/Costa_Rica",
    "cr": "America/Costa_Rica",
    "panama": "America/Panama",
    "pa": "America/Panama",
    "guatemala": "America/Guatemala",
    "gt": "America/Guatemala",
    "el salvador": "America/El_Salvador",
    "sv": "America/El_Salvador",
    "honduras": "America/Tegucigalpa",
    "hn": "America/Tegucigalpa",
    "nicaragua": "America/Managua",
    "ni": "America/Managua",
    "republica dominicana": "America/Santo_Domingo",
    "dominican republic": "America/Santo_Domingo",
    "do": "America/Santo_Domingo",
    "santo domingo": "America/Santo_Domingo",
    "puerto rico": "America/Puerto_Rico",
    "pr": "America/Puerto_Rico",
    "cuba": "America/Havana",
    "brasil": "America/Sao_Paulo",
    "brazil": "America/Sao_Paulo",
    "br": "America/Sao_Paulo",
    "espana": "Europe/Madrid",
    "spain": "Europe/Madrid",
    "es": "Europe/Madrid",
    "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "portugal": "Europe/Lisbon",
    "pt": "Europe/Lisbon",
    "estados unidos": "America/New_York",
    "united states": "America/New_York",
    "united states of america": "America/New_York",
    "usa": "America/New_York",
    "us": "America/New_York",
    "eeuu": "America/New_York",
    "canada": "America/Toronto",
    "ca": "America/Toronto",
    "reino unido": "Europe/London",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "gb": "Europe/London",
    "irlanda": "Europe/Dublin",
    "ireland": "Europe/Dublin",
    "francia": "Europe/Paris",
    "france": "Europe/Paris",
    "alemania": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "italia": "Europe/Rome",
    "italy": "Europe/Rome",
    "paises bajos": "Europe/Amsterdam",
    "netherlands": "Europe/Amsterdam",
    "suiza": "Europe/Zurich",
    "switzerland": "Europe/Zurich",
    "israel": "Asia/Jerusalem",
    "emiratos arabes unidos": "Asia/Dubai",
    "united arab emirates": "Asia/Dubai",
    "india": "Asia/Kolkata",
    "australia": "Australia/Sydney",
}

_REGION_ZONES: dict[str, dict[str, str]] = {
    "America/Mexico_City": {
        "baja california": "America/Tijuana",
        "tijuana": "America/Tijuana",
        "mexicali": "America/Tijuana",
        "ensenada": "America/Tijuana",
        "sonora": "America/Hermosillo",
        "hermosillo": "America/Hermosillo",
        "baja california sur": "America/Mazatlan",
        "los cabos": "America/Mazatlan",
        "sinaloa": "America/Mazatlan",
        "mazatlan": "America/Mazatlan",
        "culiacan": "America/Mazatlan",
        "nayarit": "America/Mazatlan",
        "chihuahua": "America/Chihuahua",
        "ciudad juarez": "America/Ciudad_Juarez",
        "quintana roo": "America/Cancun",
        "cancun": "America/Cancun",
        "playa del carmen": "America/Cancun",
    },
    "America/New_York": {
        **{s: "America/Chicago" for s in (
            "alabama", "arkansas", "illinois", "iowa", "kansas", "louisiana", "minnesota",
            "mississippi", "missouri", "nebraska", "north dakota", "oklahoma", "south dakota",
            "tennessee", "texas", "wisconsin", "chicago", "houston", "dallas", "austin",
            "san antonio", "nashville", "minneapolis",
        )},
        **{s: "America/Denver" for s in (
            "colorado", "idaho", "montana", "new mexico", "utah", "wyoming", "denver",
            "salt lake city",
        )},
        "arizona": "America/Phoenix",
        "phoenix": "America/Phoenix",
        **{s: "America/Los_Angeles" for s in (
            "california", "nevada", "oregon", "washington state", "los angeles", "san francisco",
            "san diego", "san jose", "seattle", "portland", "las vegas", "silicon valley",
            "bay area",
        )},
        "alaska": "America/Anchorage",
        "hawaii": "Pacific/Honolulu",
    },
    "America/Toronto": {
        "british columbia": "America/Vancouver",
        "vancouver": "America/Vancouver",
        "alberta": "America/Edmonton",
        "calgary": "America/Edmonton",
        "edmonton": "America/Edmonton",
        "saskatchewan": "America/Regina",
        "manitoba": "America/Winnipeg",
        "winnipeg": "America/Winnipeg",
        "nova scotia": "America/Halifax",
        "new brunswick": "America/Halifax",
        "halifax": "America/Halifax",
        "newfoundland": "America/St_Johns",
    },
    "Europe/Madrid": {
        "canarias": "Atlantic/Canary",
        "islas canarias": "Atlantic/Canary",
        "canary islands": "Atlantic/Canary",
        "tenerife": "Atlantic/Canary",
        "santa cruz de tenerife": "Atlantic/Canary",
        "las palmas": "Atlantic/Canary",
        "las palmas de gran canaria": "Atlantic/Canary",
        "gran canaria": "Atlantic/Canary",
    },
    "Europe/Lisbon": {
        "azores": "Atlantic/Azores",
        "acores": "Atlantic/Azores",
        "madeira": "Atlantic/Madeira",
    },
    "America/Santiago": {
        "magallanes": "America/Punta_Arenas",
        "punta arenas": "America/Punta_Arenas",
        "isla de pascua": "Pacific/Easter",
    },
    "America/Guayaquil": {
        "galapagos": "Pacific/Galapagos",
    },
    "Australia/Sydney": {
        "western australia": "Australia/Perth",
        "perth": "Australia/Perth",
        "queensland": "Australia/Brisbane",
        "brisbane": "Australia/Brisbane",
        "south australia": "Australia/Adelaide",
        "adelaide": "Australia/Adelaide",
        "northern territory": "Australia/Darwin",
    },
}

# Prefijos E.164 → zona. +1 se resuelve aparte (NANP compartido).
_CALLING_CODE_ZONES: dict[str, str] = {
    "54": "America/Argentina/Buenos_Aires",
    "56": "America/Santiago",
    "52": "America/Mexico_City",
    "57": "America/Bogota",
    "51": "America/Lima",
    "598": "America/Montevideo",
    "593": "America/Guayaquil",
    "595": "America/Asuncion",
    "591": "America/La_Paz",
    "58": "America/Caracas",
    "506": "America/Costa_Rica",
    "507": "America/Panama",
    "502": "America/Guatemala",
    "503": "America/El_Salvador",
    "504": "America/Tegucigalpa",
    "505": "America/Managua",
    "53": "America/Havana",
    "55": "America/Sao_Paulo",
    "34": "Europe/Madrid",
    "351": "Europe/Lisbon",
    "44": "Europe/London",
    "353": "Europe/Dublin",
    "33": "Europe/Paris",
    "49": "Europe/Berlin",
    "39": "Europe/Rome",
    "31": "Europe/Amsterdam",
    "41": "Europe/Zurich",
    "972": "Asia/Jerusalem",
    "971": "Asia/Dubai",
    "91": "Asia/Kolkata",
}

_NANP_AREA_ZONES: dict[str, str] = {
    "809": "America/Santo_Domingo",
    "829": "America/Santo_Domingo",
    "849": "America/Santo_Domingo",
    "787": "America/Puerto_Rico",
    "939": "America/Puerto_Rico",
}

_ALL_REGIONS: dict[str, str] = {k: v for regions in _REGION_ZONES.values() for k, v in regions.items()}


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def zone_from_location(text: str | None) -> str | None:
    """Zona IANA a partir de texto libre tipo 'Monterrey, Nuevo León, México'."""
    if not text or not str(text).strip():
        return None
    parts = [p for p in (_norm(x) for x in re.split(r"[,;/|·\-–]+", str(text))) if p]
    if not parts:
        return None
    # Códigos de 2 letras al final: "San Francisco, CA" es California, no Canadá.
    country_zone = next(
        (_COUNTRY_ZONES[p] for p in reversed(parts) if len(p) > 2 and p in _COUNTRY_ZONES), None
    )
    regions = _REGION_ZONES.get(country_zone or "", {}) if country_zone else _ALL_REGIONS
    for p in parts:
        if p in regions:
            return regions[p]
    if country_zone:
        return country_zone
    return next((_COUNTRY_ZONES[p] for p in reversed(parts) if p in _COUNTRY_ZONES), None)


def zone_from_phone(number: str | None) -> str | None:
    raw = (number or "").strip()
    if not raw:
        return None
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not raw.startswith("+"):
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if digits.startswith("1"):
        return _NANP_AREA_ZONES.get(digits[1:4])
    for n in (3, 2):
        zone = _CALLING_CODE_ZONES.get(digits[:n])
        if zone:
            return zone
    return None


def _single_zone(text: str | None) -> str | None:
    if not text or not str(text).strip():
        return None
    zones = {z for z in (zone_from_location(p) for p in re.split(r"[,;/|]+|\by\b|\band\b", str(text))) if z}
    return zones.pop() if len(zones) == 1 else None


def timezone_hint_from_text(text: str | None) -> str | None:
    """Si el prospecto dice '11 hs Chile' / 'hora México', usa esa zona (no la del vendedor)."""
    folded = _norm(text or "")
    if not folded:
        return None
    names = sorted(_COUNTRY_ZONES.items(), key=lambda kv: len(kv[0]), reverse=True)
    for name, zone in names:
        if len(name) < 3:
            continue
        if re.search(rf"\b{re.escape(name)}\b", folded):
            return zone
    for name, zone in _ALL_REGIONS.items():
        if len(name) < 4:
            continue
        if re.search(rf"\b{re.escape(name)}\b", folded):
            return zone
    return None


def operator_timezone(*, user: Any = None, campaign: Any = None) -> str:
    """Zona del vendedor (global). No se pide por campaña: viaja → la cambia una vez."""
    seller = getattr(campaign, "seller", None) if campaign is not None else None
    for raw in (
        getattr(user, "timezone", None),
        getattr(seller, "timezone", None),
        getattr(campaign, "timezone", None) if campaign is not None else None,
    ):
        zone = (raw or "").strip()
        if zone:
            return zone
    return "America/Argentina/Buenos_Aires"


def prospect_timezone(prospect: Any, campaign: Any = None) -> str | None:
    """
    Zona local del prospecto: país/región → teléfono/WhatsApp → país objetivo de la campaña
    (si es uno solo) → zona de la campaña.
    """
    zone = zone_from_location(getattr(prospect, "country", None))
    if zone:
        return zone
    for attr in ("whatsapp", "phone"):
        zone = zone_from_phone(getattr(prospect, attr, None))
        if zone:
            return zone
    zone = _single_zone(getattr(campaign, "target_country", None))
    if zone:
        return zone
    return getattr(campaign, "timezone", None) or None
