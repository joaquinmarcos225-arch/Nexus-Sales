"""Casos de agenda que completan el flujo base: elegir un horario ofrecido, mail de la invitación y cancelar."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.conversation_intelligence import fold_accents

logger = logging.getLogger(__name__)

OFFERED_TTL_DAYS = 7

# Local part con unicode (Joaquín.test@gmail.com); no cortar en la tilde.
_EMAIL_RE = re.compile(
    r"[\w.%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    re.UNICODE,
)

_ORDINALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(?:el|la)\s+(?:primer[oa]?|1(?:ro|ra|°|º))(?:\s+(?:opcion|horario))?\b"), 0),
    (re.compile(r"\b(?:el|la)\s+(?:segund[oa]|2(?:do|da|°|º))(?:\s+(?:opcion|horario))?\b"), 1),
    (re.compile(r"\b(?:el|la)\s+(?:tercer[oa]?|3(?:ro|ra|°|º))(?:\s+(?:opcion|horario))?\b"), 2),
    (re.compile(r"\b(?:el|la)\s+ultim[oa]\b"), -1),
    (re.compile(r"\bopcion\s*(?:n(?:ro|°|º)?\.?\s*)?1\b"), 0),
    (re.compile(r"\bopcion\s*(?:n(?:ro|°|º)?\.?\s*)?2\b"), 1),
    (re.compile(r"\bopcion\s*(?:n(?:ro|°|º)?\.?\s*)?3\b"), 2),
    (re.compile(r"\bprimer[oa]?\s+(?:opcion|horario)\b"), 0),
    (re.compile(r"\bsegund[oa]\s+(?:opcion|horario)\b"), 1),
    (re.compile(r"\btercer[oa]?\s+(?:opcion|horario)\b"), 2),
)
# «el 2» a secas no cuenta: puede ser el día 2 del mes.
_WEEKDAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_HOUR_RE = re.compile(r"\b(?:a\s+las|las|de\s+las)\s+(\d{1,2})(?:[:.](\d{2}))?\b")

_CANCEL_RE = re.compile(
    r"\b(cancel\w*|suspend\w*|anul\w*)\b"
    r"|\bno\s+(?:voy\s+a\s+)?(?:poder|podre|puedo)\s+(?:ir|asistir|llegar|conectarme|estar|participar)\b"
    r"|\bse\s+me\s+complico\b"
)

_RESCHEDULE_RE = re.compile(
    r"\botro\s+(?:dia|horario|momento|dia\s+y\s+horario)\b"
    r"|\b(?:reprogram\w*|reagend\w*|posterg\w*)\b"
    r"|\b(?:cambiar|mover|correr|pasar)(?:la|lo)?\s+(?:la\s+|el\s+)?(?:reunion|llamada|call|horario|dia|fecha|para)\b"
    r"|\b(?:cambiarla|moverla|correrla|pasarla)\b"
    r"|\bno\s+(?:voy\s+a\s+)?(?:poder|podre|puedo|llego)\b(?=[^.?!]*\b(?:" + "|".join(_WEEKDAYS)
    + r"|manana|hoy|ese\s+dia|ese\s+horario|esa\s+hora|a\s+esa|la\s+reunion|la\s+llamada|al\s+final)\b)"
    r"|\b(?:" + "|".join(_WEEKDAYS) + r"|manana|hoy|al\s+final)\b[^.?!]*\bno\s+(?:voy\s+a\s+)?(?:poder|podre|puedo|llego)\b"
)


def _state(prospect: Any) -> dict[str, Any]:
    raw = getattr(prospect, "meeting_scheduling_state", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(prospect: Any, data: dict[str, Any]) -> None:
    prospect.meeting_scheduling_state = json.dumps(data) if data else None


def _parse_iso(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def remember_offered_slots(prospect: Any, slots: list[str] | list[datetime]) -> None:
    iso = [s.isoformat() if isinstance(s, datetime) else str(s) for s in slots if s]
    if not iso:
        return
    data = _state(prospect)
    data["offered"] = iso
    data["offered_at"] = datetime.now(UTC).isoformat()
    _save(prospect, data)


def offered_slots(prospect: Any) -> list[datetime]:
    data = _state(prospect)
    at = _parse_iso(data.get("offered_at"))
    if at is None or datetime.now(UTC) - at > timedelta(days=OFFERED_TTL_DAYS):
        return []
    now = datetime.now(UTC)
    out = [dt for dt in (_parse_iso(s) for s in data.get("offered") or []) if dt and dt > now]
    return out


def recover_offered_slots_from_thread(
    db: Any,
    prospect: Any,
    *,
    timezone: str,
) -> list[datetime]:
    """Si el CTA ofreció 2 horarios pero no quedó en state, rearmarlos desde el hilo."""
    existing = offered_slots(prospect)
    if existing:
        return existing
    if db is None or not hasattr(db, "scalars"):
        return []
    try:
        from sqlalchemy import select

        from app.models.outreach import OutreachMessage
        from app.services.meeting_slot_parser import parse_meeting_slots
    except Exception:
        return []

    pid = getattr(prospect, "id", None)
    if pid is None:
        return []
    try:
        rows = db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.prospect_id == int(pid),
                OutreachMessage.direction == "outbound",
                OutreachMessage.channel == "whatsapp",
            )
            .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
            .limit(12)
        ).all()
    except Exception:
        return []
    def _looks_like_offer(low: str) -> bool:
        if "te queda bien" in low or "llamada breve" in low:
            if " o " in low or " u " in low:
                return True
        if "cual preferis" in low:
            return True
        if "horarios si estan disponibles" in low or "estan disponibles" in low:
            return True
        if re.search(
            r"(?m)^\s*[\*\-•]?\s*(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+\d{1,2}",
            low,
        ):
            return True
        return False

    plains: list[str] = [(getattr(r, "message", None) or "").strip() for r in rows]
    # Meow: a veces la oferta llegó al teléfono pero no quedó en outreach_messages.
    try:
        from sqlalchemy import select as _select

        from app.models.whatsapp_meow import WhatsappMeowMessage
        from app.services.whatsapp_assisted_service import prospect_whatsapp_digits

        digits = (prospect_whatsapp_digits(prospect) or "").strip()
        tail = digits[-10:] if len(digits) >= 10 else digits
        if tail:
            meow_rows = db.scalars(
                _select(WhatsappMeowMessage)
                .where(
                    WhatsappMeowMessage.direction == "out",
                    WhatsappMeowMessage.peer.contains(tail),
                )
                .order_by(WhatsappMeowMessage.id.desc())
                .limit(20)
            ).all()
            plains.extend((getattr(r, "body", None) or "").strip() for r in meow_rows)
    except Exception:
        logger.debug("recover offered from meow failed", exc_info=True)

    for plain in plains:
        if not plain:
            continue
        low = fold_accents(plain.lower())
        if not _looks_like_offer(low):
            continue
        slots = parse_meeting_slots(plain, timezone=timezone)
        for line in plain.splitlines():
            line = line.strip().lstrip("*-• ").strip()
            if not line:
                continue
            if not re.search(
                r"(?i)\b(lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo)\b",
                line,
            ):
                continue
            for s in parse_meeting_slots(line, timezone=timezone):
                if s not in slots:
                    slots.append(s)
        future = [s for s in slots if s > datetime.now(UTC)]
        if len(future) >= 1:
            remember_offered_slots(prospect, future)
            return offered_slots(prospect) or future
    return []


def clear_offered_slots(prospect: Any) -> None:
    data = _state(prospect)
    data.pop("offered", None)
    data.pop("offered_at", None)
    _save(prospect, data)


def inbound_is_short_yes(text: str | None) -> bool:
    """«dale», «ok», «sí»: aceptación corta (horario ofrecido o mail propuesto)."""
    folded = fold_accents((text or "").strip().lower())
    if not folded or len(folded) > 60:
        return False
    return bool(_CONFIRM_EMAIL_RE.match(folded))


def pick_offered_slot(text: str | None, offered: list[datetime], *, timezone: str) -> datetime | None:
    """El prospecto elige uno de los horarios que le ofrecimos («el segundo», «el jueves», «el de las 15»)."""
    if not offered:
        return None
    folded = fold_accents((text or "").lower())
    if not folded.strip():
        return None
    for pat, idx in _ORDINALS:
        if pat.search(folded):
            return offered[idx] if idx < len(offered) else None
    if inbound_is_short_yes(text):
        return offered[0]

    tz = ZoneInfo(timezone)
    days = [i for i, d in enumerate(_WEEKDAYS) if re.search(rf"\b{d}\b", folded)]
    hour_m = _HOUR_RE.search(folded)
    if not days and not hour_m:
        return None
    hits = []
    for slot in offered:
        local = slot.astimezone(tz)
        if days and local.weekday() not in days:
            continue
        if hour_m:
            hour = int(hour_m.group(1))
            minute = int(hour_m.group(2) or 0)
            if hour < 8:
                hour += 12
            if (local.hour, local.minute) != (hour, minute):
                continue
        hits.append(slot)
    return hits[0] if len(hits) == 1 else None


def extract_email(text: str | None) -> str | None:
    """Extrae mail del chat. Acepta tildes en el local (Joaquín → joaquin)."""
    raw = (text or "").strip()
    if not raw:
        return None
    m = _EMAIL_RE.search(raw)
    if not m:
        return None
    em = m.group(0).strip(".").lower()
    if "@" not in em:
        return None
    local, _, domain = em.partition("@")
    # Gmail/Calendar van mejor con ASCII en el local.
    local_ascii = fold_accents(local).replace(" ", "")
    domain_ascii = fold_accents(domain).replace(" ", "")
    if not local_ascii or "@" in local_ascii or not domain_ascii or "." not in domain_ascii:
        return None
    return f"{local_ascii}@{domain_ascii}"


def inbound_cancels_meeting(text: str | None) -> bool:
    folded = fold_accents((text or "").lower())
    return bool(_CANCEL_RE.search(folded)) or inbound_asks_reschedule(text)


def inbound_asks_reschedule(text: str | None) -> bool:
    """Pide otro día/horario sin dar uno nuevo («el jueves no puedo, ¿puede ser otro día?»)."""
    folded = fold_accents((text or "").lower())
    return bool(_RESCHEDULE_RE.search(folded))


_CHANNEL: ContextVar[str] = ContextVar("nexus_booking_channel", default="email")
_SDR_COUNTRY: ContextVar[str] = ContextVar("nexus_sdr_country", default="")

_CONFIRM_EMAIL_RE = re.compile(
    r"^\W*(?:si|sip|sii+|dale|ok|okey|okay|perfecto|correcto|exacto|genial|de\s+una|claro|joya|barbaro|"
    r"buenisimo|ese|ese\s+mismo|a\s+ese|al\s+mismo|ese\s+(?:esta|es)\s+bien|esta\s+bien|"
    r"si,?\s+(?:a\s+)?ese(?:\s+mismo)?|si,?\s+(?:esta\s+)?bien|si,?\s+dale|si,?\s+correcto|si,?\s+perfecto)"
    r"(?:[\s,.!]+(?:gracias|dale|perfecto|ese|mandala|enviala|si))*\W*$"
)


@contextmanager
def booking_channel(channel: str | None) -> Iterator[None]:
    """Canal de la conversación: por mail se invita al mail del hilo; en chats hay que pedirlo/confirmarlo."""
    ch = (channel or "email").strip().lower()
    token = _CHANNEL.set("email" if not ch or "mail" in ch else ch)
    try:
        yield
    finally:
        _CHANNEL.reset(token)


def current_booking_channel() -> str:
    return _CHANNEL.get()


def chat_channel_active() -> bool:
    return current_booking_channel() != "email"


def current_sdr_country() -> str:
    return (_SDR_COUNTRY.get() or "").strip()


@contextmanager
def sdr_country(country: str | None) -> Iterator[None]:
    token = _SDR_COUNTRY.set((country or "").strip())
    try:
        yield
    finally:
        _SDR_COUNTRY.reset(token)


def mark_invite_pending(prospect: Any, meeting_id: Any) -> None:
    data = _state(prospect)
    data["invite_pending_meeting_id"] = int(meeting_id) if meeting_id else None
    _save(prospect, data)


def invite_pending_meeting_id(prospect: Any) -> int | None:
    raw = _state(prospect).get("invite_pending_meeting_id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def clear_invite_pending(prospect: Any) -> None:
    data = _state(prospect)
    if data.pop("invite_pending_meeting_id", None) is not None:
        _save(prospect, data)


def inbound_confirms_email(text: str | None) -> bool:
    """«Sí, a ese», «dale», «correcto»: confirma el mail que le propusimos."""
    return inbound_is_short_yes(text)


_CONFIRM_KNOWN_EMAIL_RE = re.compile(
    r"("
    r"si,?\s+(?:a\s+)?ese(?:\s+(?:mismo|mail|correo|email))?|"
    r"si(?:\s+ese)?\s+es\s+el\s+(?:mail|correo|email)|"
    r"ese\s+es\s+el\s+(?:mail|correo|email)|"
    r"el\s+(?:mail|correo|email)\s+es\s+ese|"
    r"(?:a\s+)?ese(?:\s+(?:mail|correo|email)).{0,24}(?:perfecto|bien|ok|correcto|joya)|"
    r"ese\s+(?:mail|correo|email).{0,16}(?:esta|ezta|esta)\s+(?:perfecto|bien|ok)|"
    r"(?:el\s+)?(?:mail|correo|email).{0,10}(?:esta|ezta|esta)\s+perfecto|"
    r"a\s+ese\s+(?:esta|ezta|esta)\s+perfecto|"
    r"ese\s+mismo(?:\s+(?:mail|correo|email))?"
    r")",
    re.IGNORECASE,
)


def inbound_confirms_proposed_email(prospect: Any, text: str | None) -> bool:
    """Confirmó el mail que le propusimos, aunque venga junto con un horario nuevo."""
    known = proposed_invite_email(prospect) or (getattr(prospect, "email", None) or "").strip()
    if not known:
        return False
    folded = fold_accents((text or "").strip().lower())
    if not folded:
        return False
    if _CONFIRM_KNOWN_EMAIL_RE.search(folded):
        return True
    if inbound_is_short_yes(text) and not offered_slots(prospect):
        return True
    return False


_REFUSES_EMAIL_RE = re.compile(
    r"("
    r"no\s+(?:te\s+)?(?:paso|mando|doy|tengo|uso|manejo)\s+(?:el\s+|mi\s+)?(?:mail|correo|email)|"
    r"no\s+(?:tengo|uso|manejo)\s+(?:mail|correo|email)|"
    r"sin\s+(?:mail|correo|email)|"
    r"(?:no|ni)\s+(?:me\s+)?(?:va\s+a\s+)?(?:pasar|mandar|dar)\s+(?:el\s+|mi\s+)?(?:mail|correo|email)|"
    r"prefer[ií]a?\s+(?:sin\s+mail|el\s+link|por\s+(?:wa|whatsapp|aca|aqui))|"
    r"mandame\s+el\s+link|"
    r"pasame\s+el\s+link\s+(?:de\s+)?(?:meet|la\s+reunion|la\s+llamada)|"
    r"mejor\s+(?:el\s+)?link|"
    r"no\s+hace\s+falta\s+(?:el\s+)?(?:mail|correo|email)"
    r")",
    re.IGNORECASE,
)


def inbound_refuses_email(text: str | None) -> bool:
    """El prospecto no va a pasar mail: hay que crear el Meet y mandarle el link por el chat."""
    folded = fold_accents((text or "").strip().lower())
    if not folded or len(folded) > 220:
        return False
    if extract_email(text):
        return False
    return bool(_REFUSES_EMAIL_RE.search(folded))


PENDING_SLOT_TTL_DAYS = 3


def mark_proposed_invite_email(prospect: Any, email: str | None) -> None:
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        return
    data = _state(prospect)
    data["proposed_invite_email"] = em
    _save(prospect, data)


def proposed_invite_email(prospect: Any) -> str | None:
    raw = (_state(prospect).get("proposed_invite_email") or "").strip().lower()
    return raw if raw and "@" in raw else None


def clear_proposed_invite_email(prospect: Any) -> None:
    data = _state(prospect)
    if data.pop("proposed_invite_email", None) is not None:
        _save(prospect, data)


def mark_chat_email_confirmed(prospect: Any) -> None:
    data = _state(prospect)
    if data.get("chat_email_confirmed"):
        return
    data["chat_email_confirmed"] = True
    _save(prospect, data)


def chat_email_confirmed(prospect: Any) -> bool:
    return bool(_state(prospect).get("chat_email_confirmed"))


def clear_chat_email_confirmed(prospect: Any) -> None:
    data = _state(prospect)
    if data.pop("chat_email_confirmed", None) is not None:
        _save(prospect, data)


def remember_pending_slot(prospect: Any, slot: datetime, *, duration: int, is_reschedule: bool) -> None:
    """Horario aceptado, esperando el mail del prospecto para crear la reunión con él de invitado."""
    data = _state(prospect)
    data["pending_slot"] = {
        "at": slot.isoformat(),
        "duration": int(duration),
        "is_reschedule": bool(is_reschedule),
        "saved_at": datetime.now(UTC).isoformat(),
    }
    _save(prospect, data)


def pending_slot(prospect: Any) -> dict[str, Any] | None:
    raw = _state(prospect).get("pending_slot")
    if not isinstance(raw, dict):
        return None
    at = _parse_iso(raw.get("at"))
    saved = _parse_iso(raw.get("saved_at"))
    now = datetime.now(UTC)
    if at is None or at <= now or (saved and now - saved > timedelta(days=PENDING_SLOT_TTL_DAYS)):
        return None
    return {"at": at, "duration": int(raw.get("duration") or 30), "is_reschedule": bool(raw.get("is_reschedule"))}


def reserved_slot_for_email_booking(prospect: Any) -> dict[str, Any] | None:
    """Horario acordado pendiente de mail: no expira por TTL mientras falte el mail."""
    active = pending_slot(prospect)
    if active is not None:
        return active
    raw = _state(prospect).get("pending_slot")
    if not isinstance(raw, dict):
        return None
    at = _parse_iso(raw.get("at"))
    now = datetime.now(UTC)
    if at is None or at <= now:
        return None
    return {
        "at": at,
        "duration": int(raw.get("duration") or 30),
        "is_reschedule": bool(raw.get("is_reschedule")),
    }


def clear_pending_slot(prospect: Any) -> None:
    data = _state(prospect)
    if data.pop("pending_slot", None) is not None:
        _save(prospect, data)


def _localize(text: str) -> str:
    from app.services.outreach_language import localize_sdr_copy

    return localize_sdr_copy(text, country=current_sdr_country())


def build_ask_email_reply(
    *,
    prospect_name: str | None,
    slot: datetime,
    timezone: str,
    known_email: str | None = None,
    already_asked: bool = False,
) -> str:
    from app.services.meeting_booking import _format_slot_for_confirmation

    when = _format_slot_for_confirmation(slot, timezone)
    head = f"Genial, {when} entonces."
    em = (known_email or "").strip()
    if em:
        return _localize(
            f"{head}\n\n"
            f"¿Te mando la invitación a {em} o preferís otro mail?"
        )
    # Ya pedimos el mail en este hilo: confirmar horario nuevo sin repetir la misma pregunta.
    if already_asked:
        return _localize(
            f"{head}\n\n"
            "Cuando me pases el mail te mando la invitación."
        )
    return _localize(
        f"{head}\n\n"
        "¿Me pasás tu mail así te mando la invitación?"
    )


def build_mail_required_reply(
    *, prospect_name: str | None, slot: datetime, timezone: str
) -> str:
    """Si no quieren pasar mail: no hay reunión en Calendar."""
    from app.services.meeting_booking import _format_slot_for_confirmation

    when = _format_slot_for_confirmation(slot, timezone)
    return _localize(
        f"Para {when} necesito un mail para mandarte la invitación. "
        f"Sin mail no puedo agendarla. ¿Me lo pasás?"
    )


def close_chat_after_booking(prospect: Any) -> None:
    """
    Tras confirmar reunión + invite: limpia pendientes de agenda y marca el chat cerrado
    para que Nexus no vuelva a prospectar ni auto-contestar ese hilo.
    """
    clear_pending_slot(prospect)
    clear_offered_slots(prospect)
    clear_invite_pending(prospect)
    clear_proposed_invite_email(prospect)
    data = _state(prospect)
    commercial = data.get("commercial") if isinstance(data.get("commercial"), dict) else {}
    commercial = dict(commercial)
    commercial["auto_reply_closed"] = True
    commercial["status"] = "booked"
    commercial["closed_at"] = datetime.now(UTC).isoformat()
    data["commercial"] = commercial
    _save(prospect, data)
    # Sacá colas asistidas: la secuencia ya no debe tocar a esta persona.
    if getattr(prospect, "linkedin_assisted_draft", None):
        prospect.linkedin_assisted_draft = None
    if getattr(prospect, "whatsapp_assisted_draft", None):
        prospect.whatsapp_assisted_draft = None


def clear_post_meeting_silence_for_new_sequence(prospect: Any) -> None:
    """
    Al iniciar una tanda nueva: no heredar silencio de una reunión previa.
    No toca Google Calendar ni borra meetings — solo desbloquea inbound/auto-reply.
    """
    from app.models.enums import PipelineStage, ProspectStatus
    from app.services import prospect_commercial_state as pcs

    cs = (getattr(prospect, "commercial_state", None) or "").strip()
    if cs in (pcs.COMMERCIAL_REUNION_AGENDADA, pcs.COMMERCIAL_REUNION_PENDIENTE):
        prospect.commercial_state = pcs.COMMERCIAL_PROSPECCION
    status = (getattr(prospect, "status", None) or "").strip().lower()
    if status == ProspectStatus.meeting_booked.value:
        prospect.status = ProspectStatus.compatible.value
        prospect.pipeline_stage = PipelineStage.nuevo.value
    data = _state(prospect)
    commercial = data.get("commercial") if isinstance(data.get("commercial"), dict) else None
    if isinstance(commercial, dict) and (
        commercial.get("auto_reply_closed")
        or commercial.get("status") == "booked"
        or commercial.get("closed_at")
    ):
        commercial = dict(commercial)
        commercial.pop("auto_reply_closed", None)
        commercial.pop("closed_at", None)
        if commercial.get("status") == "booked":
            commercial.pop("status", None)
        data = dict(data)
        if commercial:
            data["commercial"] = commercial
        else:
            data.pop("commercial", None)
        _save(prospect, data)


def finalize_meeting_after_confirmation_sent(
    db: Any,
    *,
    prospect: Any,
    campaign: Any,
    reply_body: str | None = None,
) -> bool:
    """
    Outbound de confirmación ya salió → recién ahí estado agendado + cierre de chat.
    Devuelve True si aplicó el finalize (o ya estaba cerrado).
    """
    from app.models.enums import PipelineStage, ProspectStatus
    from app.services import multichannel_sequence as mseq
    from app.services import pipeline_sync
    from app.services import prospect_commercial_state as pcs

    if chat_closed_after_booking(prospect):
        return True

    status = (getattr(prospect, "status", None) or "").strip().lower()
    has_closing = looks_like_booking_closing_reply(reply_body)
    # Solo con el aviso de invite/confirmación (o si ya venía meeting_booked).
    if status != ProspectStatus.meeting_booked.value and not has_closing:
        return False

    prospect.status = ProspectStatus.meeting_booked.value
    prospect.pipeline_stage = PipelineStage.reunion_agendada.value
    prospect.commercial_state = pcs.COMMERCIAL_REUNION_AGENDADA
    mseq.enforce_meeting_priority_over_sequence(db, prospect, campaign)
    pipeline_sync.sync_pipeline_from_status(prospect)
    close_chat_after_booking(prospect)
    if getattr(prospect, "id", None) is not None:
        cancel_pending_auto_replies_for_prospect(
            db, int(prospect.id), reason="meeting_booked_closing_sent"
        )
    return True


def chat_closed_after_booking(prospect: Any) -> bool:
    """True solo después del aviso final (invite + gracias). Antes Nexus aún puede mandarlo."""
    data = _state(prospect)
    commercial = data.get("commercial") if isinstance(data.get("commercial"), dict) else {}
    return bool(commercial.get("auto_reply_closed"))


def should_ignore_inbound_after_meeting(prospect: Any) -> str | None:
    """
    Reunión ya confirmada → sacar foco de la cuenta:
    Gmail/WA no scanean inbound; Brain/Policy no levantan agent; cero auto-reply.
    Reagenda / follow-up = humano por ahora.

    Cuenta: cierre post-confirmación, `status=meeting_booked`, grupo `reuniones`.
    NO basta `commercial_state=reunion_agendada` solo: tras reiniciar secuencia ese
    campo puede quedar pegado y mataba inbound (Mia) sin status/grupo de reunión.
    """
    if chat_closed_after_booking(prospect):
        return "meeting_booked"
    status = (getattr(prospect, "status", None) or "").strip().lower()
    if status == "meeting_booked":
        return "meeting_booked"
    if (getattr(prospect, "sequence_group", None) or "").strip().lower() == "reuniones":
        return "meeting_booked"
    return None


def auto_reply_silenced(prospect: Any) -> str | None:
    """
    Motivo por el que Nexus NO debe mirar ni auto-contestar este chat.
    None = puede seguir (p. ej. pausa automática por inbound, la IA conversa).

    Post-agendar (status meeting_booked / grupo reuniones / chat cerrado
    tras «invite enviado / nos vemos»): sacar foco — Gmail no scanea,
    Policy corta el Brain, cero agent.
    `commercial_state=reunion_agendada` solo NO silencia (puede quedar pegado
    tras reiniciar secuencia).

    También: Pausar / Handoff del SDR (manual_pause), ai_paused.
    """
    if bool(getattr(prospect, "ai_paused", False)):
        return "ai_paused"
    ignore = should_ignore_inbound_after_meeting(prospect)
    if ignore:
        return ignore
    state = _state(prospect)
    if state.get("manual_pause"):
        return "manual_pause"
    if state.get("meeting_done_id"):
        return "meeting_done"
    return None


def looks_like_booking_closing_reply(text: str | None) -> bool:
    """Último aviso post-agenda: invite / gracias / nos vemos."""
    low = fold_accents((text or "").strip().lower())
    if not low:
        return False
    return bool(
        re.search(
            r"("
            r"te\s+mande\s+la\s+invitacion|"
            r"te\s+envie\s+la\s+invitacion|"
            r"invitacion\s+a\s+\S+@|"
            r"nos\s+vemos\s+(ahi|alli|alla)|"
            r"gracias\b.{0,48}\bnos\s+vemos|"
            r"listo\b.{0,48}\bte\s+agende"
            r")",
            low,
        )
    )


def cancel_pending_auto_replies_for_prospect(db: Any, prospect_id: int, *, reason: str = "") -> int:
    """Cancela tasks de auto-reply pendientes (WA/mail) para que no disparen después del stop."""
    try:
        from app.services.inbound_auto_reply import cancel_all_pending_inbound_auto_reply_tasks

        return cancel_all_pending_inbound_auto_reply_tasks(
            db, prospect_id=int(prospect_id), reason=reason or "silenced"
        )
    except Exception:
        logger.debug(
            "cancel pending auto-replies failed prospect_id=%s", prospect_id, exc_info=True
        )
        return 0


def build_invite_sent_reply(
    *,
    prospect_name: str | None,
    slot: datetime,
    timezone: str,
    email: str,
    meet_link: str | None = None,
    via_chat: bool = False,
) -> str:
    """Último mensaje post-agenda: aviso de invite + gracias. Después Nexus no sigue el chat."""
    from app.services.meeting_booking import _first_name, _format_slot_for_confirmation

    first = _first_name(prospect_name)
    when = _format_slot_for_confirmation(slot, timezone)
    link = (meet_link or "").strip()
    if via_chat and link:
        return _localize(
            f"Listo {first}, te agendé para {when}.\n\n"
            f"Te mandé la invitación a {email} (con Meet).\n\n"
            f"Link por acá:\n{link}\n\n"
            f"Gracias {first}, nos vemos ahí."
        )
    return _localize(
        f"Listo {first}, te agendé para {when}.\n\n"
        f"Te mandé la invitación a {email} (con Meet).\n\n"
        f"Gracias {first}, nos vemos ahí."
    )


def build_meet_link_only_reply(
    *,
    prospect_name: str | None,
    slot: datetime,
    timezone: str,
    meet_link: str,
) -> str:
    """Sin mail del prospecto: reunión creada + link de Meet + cierre."""
    from app.services.meeting_booking import _first_name, _format_slot_for_confirmation

    first = _first_name(prospect_name)
    when = _format_slot_for_confirmation(slot, timezone)
    link = (meet_link or "").strip()
    return (
        f"Listo {first}, te agendé para {when}.\n\n"
        f"Entrá con este link:\n{link}\n\n"
        f"Gracias {first}, nos vemos ahí."
    )


def build_invite_email_line(email: str | None) -> str:
    """Aviso de invite enviado. Sin preguntar por el mail: ese chat ya se cierra."""
    em = (email or "").strip()
    if not em:
        return ""
    return f"Te mandé la invitación a {em} (con Meet)."


def build_email_updated_reply(*, prospect_name: str | None, email: str, resent: bool) -> str:
    from app.services.meeting_booking import _first_name

    first = _first_name(prospect_name)
    verb = "te reenvié" if resent else "te mandé"
    return f"Listo {first}, {verb} la invitación a {email}.\n\nGracias {first}, nos vemos ahí."


def build_canceled_reply(
    *,
    prospect_name: str | None,
    alternatives: list[datetime],
    timezone: str,
    reschedule: bool = False,
) -> str:
    from app.services.google_calendar_availability import format_slot_local
    from app.services.meeting_booking import _first_name

    first = _first_name(prospect_name)
    if reschedule:
        if alternatives:
            bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
            return (
                f"Sin problema {first}, la movemos. Tengo libres:\n{bullets}\n\n"
                "¿Cuál te queda mejor? Si ninguno te sirve, decime día y hora."
            )
        return f"Sin problema {first}, la movemos. Decime qué día y hora te queda bien y la paso."
    if alternatives:
        bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
        return (
            f"Listo {first}, cancelé la reunión.\n\n"
            f"Si querés la pasamos para otro momento. Tengo libres:\n{bullets}\n\n¿Te sirve alguno?"
        )
    return (
        f"Listo {first}, cancelé la reunión.\n\n"
        "Cuando quieras la retomamos: decime día y hora y la agendo."
    )
