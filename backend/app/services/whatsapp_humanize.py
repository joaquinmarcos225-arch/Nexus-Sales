"""Humanización WhatsApp: espera, typing, burbujas, cupos y texto.

Reglas de producto (no Gmail):
- Réplica inbound: espera al azar 8–22 s, después ritual de envío.
- Ritual: pensar 2–3,5 s (a veces menos) → typing (10 caracteres = 1 s)
  con una pausita 0,5–1 s → enviar. Cada burbuja respeta el mismo ritmo.
- En frío: 45 personas/día. Quien ya respondió: sin tope de charla.
- Follow-ups: 200/día.
- Horario laboral de toques fríos/FU: 8:30–19, lun–vie (send_window).
- Links: siempre una frase corta que diga qué es el enlace.
- Nombre / rol / empresa: solo en el primer toque frío (o follow-up sin respuesta).
  En la charla, no vocativo en cada burbuja.
- Si preguntan si es IA: negar en nombre del vendedor.
- Entre personas distintas: 30 s–1 min al azar. Misma charla no espera eso.
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from typing import Any, Callable

REPLY_WAIT_MIN_SEC = 8
REPLY_WAIT_MAX_SEC = 22
THINK_MIN_SEC = 2.0
THINK_MAX_SEC = 3.5
THINK_SHORT_CHANCE = 0.28
THINK_SHORT_MIN_SEC = 0.35
THINK_SHORT_MAX_SEC = 1.2
CHARS_PER_SEC = 10.0
MID_PAUSE_MIN_SEC = 0.5
MID_PAUSE_MAX_SEC = 1.0
COLD_PEOPLE_PER_DAY = 45
FOLLOWUPS_PER_DAY = 200
PERSON_GAP_MIN_SEC = 30
PERSON_GAP_MAX_SEC = 60

_person_lock = threading.Lock()
# seller_id -> (prospect_id, sent_monotonic, next_gap_sec)
_last_person_send: dict[int, tuple[int, float, float]] = {}
_composing_lock = threading.Lock()
# digits -> monotonic last composing
_prospect_composing_until: dict[str, float] = {}
COMPOSING_HOLD_SEC = 8.0

_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_LINK_ALREADY_RE = re.compile(
    r"(invitaci[oó]n|reuni[oó]n|agenda|agend|calendar|meet|enlace|link|"
    r"videollamada|llamada|nos vemos)",
    re.I,
)
_AI_QUESTION_RE = re.compile(
    r"("
    r"\b(sos|eres|es|estas?|are you|is this|is it)\b.{0,40}\b("
    r"una?\s+ia|un\s+bot|inteligencia\s+artificial|chatgpt|automatiz|"
    r"robot|virtual\s+assistant|ai\b|bot\b"
    r")\b"
    r"|"
    r"\b(ia|bot|chatgpt|openai)\b.{0,20}\?"
    r"|"
    r"\bte\s+contesta(s|n)?\s+(un[oa]|una)\s+(ia|bot|robot)\b"
    r"|"
    r"\bsos\s+(un\s+)?(bot|robot|chatgpt)\b"
    r")",
    re.I,
)


def _rng(rng: random.Random | None) -> random.Random:
    return rng if rng is not None else random


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def pacing_enabled() -> bool:
    """En pytest el ritmo real se apaga para no frenar los tests."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    raw = (os.getenv("NEXUS_WA_HUMANIZE_PACE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def note_prospect_composing(peer: str, *, composing: bool, now_fn: Callable[[], float] | None = None) -> None:
    digits = re.sub(r"\D", "", peer or "")
    if len(digits) < 8:
        return
    now = (now_fn or time.monotonic)()
    with _composing_lock:
        if composing:
            _prospect_composing_until[digits] = now + COMPOSING_HOLD_SEC
        else:
            _prospect_composing_until.pop(digits, None)


def prospect_is_composing(peer_or_digits: str, *, now_fn: Callable[[], float] | None = None) -> bool:
    digits = re.sub(r"\D", "", peer_or_digits or "")
    if len(digits) < 8:
        return False
    now = (now_fn or time.monotonic)()
    with _composing_lock:
        until = _prospect_composing_until.get(digits)
        if until is None:
            return False
        if until <= now:
            _prospect_composing_until.pop(digits, None)
            return False
        return True


def inbound_reply_delay_seconds(rng: random.Random | None = None) -> int:
    lo = max(0, min(180, _env_int("NEXUS_WA_REPLY_WAIT_MIN_SEC", REPLY_WAIT_MIN_SEC)))
    hi = max(lo, min(180, _env_int("NEXUS_WA_REPLY_WAIT_MAX_SEC", REPLY_WAIT_MAX_SEC)))
    if hi <= 0:
        return 0
    return _rng(rng).randint(lo, hi)


def think_seconds(rng: random.Random | None = None) -> float:
    r = _rng(rng)
    if r.random() < THINK_SHORT_CHANCE:
        return r.uniform(THINK_SHORT_MIN_SEC, THINK_SHORT_MAX_SEC)
    return r.uniform(THINK_MIN_SEC, THINK_MAX_SEC)


def typing_seconds(text: str, *, cold: bool = True) -> float:
    """Réplicas un poco más ágiles que el cold (mismo techo humano)."""
    n = max(1, len((text or "").strip()))
    default = CHARS_PER_SEC if cold else 14.0
    return n / _env_float(
        "NEXUS_WA_CHARS_PER_SEC" if cold else "NEXUS_WA_REPLY_CHARS_PER_SEC",
        default,
    )


def mid_typing_pause_seconds(rng: random.Random | None = None) -> float:
    return _rng(rng).uniform(MID_PAUSE_MIN_SEC, MID_PAUSE_MAX_SEC)


def inter_person_gap_seconds(rng: random.Random | None = None) -> int:
    lo = max(0, _env_int("NEXUS_WA_PERSON_GAP_MIN_SEC", PERSON_GAP_MIN_SEC))
    hi = max(lo, _env_int("NEXUS_WA_PERSON_GAP_MAX_SEC", PERSON_GAP_MAX_SEC))
    if hi <= 0:
        return 0
    return _rng(rng).randint(lo, hi)


def reset_person_send_gaps_for_tests() -> None:
    with _person_lock:
        _last_person_send.clear()


def wait_before_other_person(
    seller_id: int,
    prospect_id: int,
    *,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
    rng: random.Random | None = None,
) -> float:
    """Si el último envío fue a OTRA persona, espera 30–60 s (al azar). Misma persona = 0."""
    if sleep_fn is None and not pacing_enabled():
        return 0.0
    sid = int(seller_id or 0)
    pid = int(prospect_id or 0)
    if sid <= 0 or pid <= 0:
        return 0.0
    sleeper = sleep_fn or time.sleep
    now = (now_fn or time.monotonic)()
    wait = 0.0
    with _person_lock:
        prev = _last_person_send.get(sid)
        if prev is not None:
            last_pid, sent_at, gap = prev
            if last_pid != pid:
                wait = max(0.0, float(gap) - (now - sent_at))
    if wait > 0:
        sleeper(wait)
    return wait


def note_person_sent(
    seller_id: int,
    prospect_id: int,
    *,
    now_fn: Callable[[], float] | None = None,
    rng: random.Random | None = None,
) -> None:
    sid = int(seller_id or 0)
    pid = int(prospect_id or 0)
    if sid <= 0 or pid <= 0:
        return
    with _person_lock:
        _last_person_send[sid] = (pid, (now_fn or time.monotonic)(), float(inter_person_gap_seconds(rng)))


def perform_send_ritual(
    text: str,
    *,
    think: bool = True,
    set_typing: Callable[[str], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
    cold: bool = True,
) -> dict[str, float]:
    """Simula pensar → escribir (con pausa al medio) → listo para enviar."""
    sleeper = sleep_fn or time.sleep
    think_s = think_seconds(rng) if think else 0.0
    type_s = typing_seconds(text, cold=cold)
    mid_s = mid_typing_pause_seconds(rng)
    first = max(0.15, type_s * 0.55)
    rest = max(0.1, type_s - first)
    if think_s:
        sleeper(think_s)
    if set_typing is not None:
        set_typing("composing")
    sleeper(first)
    if set_typing is not None:
        set_typing("paused")
    sleeper(mid_s)
    if set_typing is not None:
        set_typing("composing")
    sleeper(rest)
    if set_typing is not None:
        set_typing("paused")
    return {"think": think_s, "typing": type_s, "mid_pause": mid_s}


def _token(*parts: Any) -> str:
    return " ".join(str(p or "").strip() for p in parts if str(p or "").strip())


def distinctive_tokens(prospect: Any) -> list[str]:
    first = ""
    name = str(getattr(prospect, "name", None) or "").strip()
    if name:
        first = name.split()[0]
    role = _token(
        getattr(prospect, "title", None)
        or getattr(prospect, "job_title", None)
        or getattr(prospect, "role", None)
    )
    company = _token(
        getattr(prospect, "company_name", None) or getattr(prospect, "company", None)
    )
    out: list[str] = []
    for raw in (first, name, role, company):
        val = (raw or "").strip()
        if val and val.lower() not in {x.lower() for x in out}:
            out.append(val)
    return out


def ensure_personalized(text: str, prospect: Any) -> str:
    """Solo para toques fríos. En réplicas no hace falta nombrar al prospecto."""
    body = (text or "").strip()
    if not body:
        return body
    tokens = [t for t in distinctive_tokens(prospect) if len(t) > 1]
    low = body.lower()
    if any(t.lower() in low for t in tokens):
        return body
    first = tokens[0] if tokens else ""
    if not first:
        return body
    return f"{first}, {body}"


def _prospect_first_token(prospect: Any) -> str:
    name = str(getattr(prospect, "name", None) or "").strip()
    return name.split()[0] if name else ""


def strip_chat_name_vocative(text: str, prospect: Any) -> str:
    """Saca 'Juan, …' / 'Genial Juan.' de réplicas. El frío ya se presentó."""
    body = (text or "").strip()
    first = _prospect_first_token(prospect)
    if not body or len(first) < 2:
        return body
    esc = re.escape(first)
    body = re.sub(rf"\bjaj(?:aj)?\s+no\s+{esc}\b,?\s*", "Jaj no, ", body, count=1, flags=re.I)
    body = re.sub(
        rf"^((?:genial|perfecto|listo|dale|gracias|entendido|s[ií])\b)(?:\s+{esc})\b[,!.]?\s*",
        r"\1. ",
        body,
        count=1,
        flags=re.I,
    )
    body = re.sub(rf"^{esc},\s+", "", body, count=1, flags=re.I)
    body = re.sub(r" {2,}", " ", body).strip()
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    return body


def annotate_links(text: str, *, inbound_text: str = "", rng: random.Random | None = None) -> str:
    body = (text or "").strip()
    if not body or not _URL_RE.search(body):
        return body
    if _LINK_ALREADY_RE.search(body):
        return body
    inbound = (inbound_text or "").lower()
    if "mail" in inbound or "correo" in inbound or "email" in inbound:
        lead = "Te dejo el enlace de la invitación:"
    elif "hora" in inbound or "día" in inbound or "dia" in inbound:
        lead = "Armé el espacio; te paso el link:"
    else:
        lead = _rng(rng).choice(
            (
                "Te dejo el enlace:",
                "Acá va el link:",
                "Te paso esto para que abras cuando puedas:",
            )
        )
    return f"{lead}\n{body}"


def pace_outbound_bubbles(
    bubbles: list[str],
    *,
    send_fn: Callable[[int, str], Any],
    set_typing: Callable[[str], None] | None = None,
    think_first: bool = True,
    should_abort: Callable[[], bool] | None = None,
    cold: bool = True,
) -> Any:
    """Envía cada burbuja con el ritual humano (o de una si el ritmo está off)."""
    last = None
    for i, bubble in enumerate(bubbles):
        if should_abort is not None and should_abort():
            break
        if pacing_enabled():
            perform_send_ritual(
                bubble,
                think=think_first if i == 0 else True,
                set_typing=set_typing,
                cold=cold,
            )
        if should_abort is not None and should_abort():
            break
        last = send_fn(i, bubble)
    return last


def prepare_outbound_text(
    text: str,
    *,
    prospect: Any | None = None,
    inbound_text: str = "",
    rng: random.Random | None = None,
    cold: bool = False,
) -> str:
    body = annotate_links(text, inbound_text=inbound_text, rng=rng)
    if prospect is None:
        return body
    if cold:
        return ensure_personalized(body, prospect)
    return strip_chat_name_vocative(body, prospect)


def is_ai_identity_question(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_AI_QUESTION_RE.search(raw))


def ai_denial_line(*, prospect: Any | None = None, operator_first: str = "") -> str:
    who = (operator_first or "").strip()
    if who:
        return f"Jaj no, te escribo yo, {who}."
    return "Jaj no, te escribo yo."


def ai_denial_reply(
    *,
    prospect: Any | None = None,
    operator_first: str = "",
    continue_with: str = "",
) -> str:
    """Niega IA como el vendedor y sigue el hilo outbound (reunión), no '¿en qué te ayudo?'."""
    line = ai_denial_line(prospect=prospect, operator_first=operator_first)
    cont = (continue_with or "").strip()
    if not cont:
        cont = "¿Qué día y horario te quedan cómodos para una charla corta?"
    return f"{line} {cont}"
