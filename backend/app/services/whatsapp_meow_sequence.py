"""Envío de toques de secuencia por WhatsApp nativo (Meow / whatsmeow)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.models.user import User
from app.models.whatsapp_dispatch import WhatsAppDispatch
from app.models.whatsapp_meow import WhatsappMeowMessage, WhatsappMeowSession
from app.services import followup_engine, pipeline_sync
from app.services.outreach_simulation import make_message
from app.services.whatsapp_assisted_service import prospect_whatsapp_digits
from app.services.whatsapp_commercial import (
    WhatsAppCommercialError,
    assert_can_send_whatsapp_meow,
)
from app.services.whatsapp_meow_service import send_text_message
from app.services.whatsapp_meow_service import _normalize_meow_peer

logger = logging.getLogger(__name__)

MEOW_OUTBOUND_NOTE = (
    "[WhatsApp · secuencia · enviado al dispositivo — entrega no confirmada por Meta]"
)

_WA_BUBBLE_SPLIT_RE = re.compile(r"\n\s*\n")
# Cold: saludo | Soy X de Y | cómo estás | valor | CTA → hasta 5.
_WA_BUBBLE_MAX = 5
_WA_BUBBLE_MAX_INBOUND = 3
_WA_BUBBLE_GAP_SEC = 0.45
# Solo cortar afirmación si terminó la frase (punto/!): evita «Genial la» / «consulta…».
_WA_AFFIRM_HEAD_RE = re.compile(
    r"^¡?(Genial|Buen[ií]simo|Perfecto|Gracias|Dale|Joya|B[aá]rbaro|Listo)"
    r"(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]{1,24})?"
    r"[.!]\s+",
    re.I,
)
# Cola colgante: artículo/preposición → la frase sigue en la próxima burbuja.
_WA_DANGLING_TAIL_RE = re.compile(
    r"(?i)\b("
    r"el|la|los|las|un|una|unos|unas|de|del|al|en|con|por|para|"
    r"que|y|o|u|mi|tu|su|les|nos|me|te|se|lo|le|a"
    r")\s*$"
)
# Apertura fría: cada una su burbuja (nunca juntar Hola + Soy + cómo estás).
_WA_OPENING_KEEP_SEPARATE_RE = re.compile(
    r"(?is)^(hola\b|buen[oa]s\b|hey\b|soy\s+\S|¿?c[oó]mo\s+est|todo\s+bien)"
)


_WA_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[¿¡\"«A-ZÁÉÍÓÚÑ])")
# Párrafos de explicación (sin \n\n) que no deben ir en una sola burbuja.
_WA_LONG_BUBBLE_SOFT = 170


def _capitalize_sentence_start(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return raw
    if raw[0].islower():
        return raw[0].upper() + raw[1:]
    return raw


def _split_single_long_clause(raw: str, *, soft_max: int) -> list[str]:
    """
    Una sola oración enorme: cortar en límites con sentido (: ; o mitad por coma).
    Nunca mid-word ni dejando «y con» colgando.
    """
    text = (raw or "").strip()
    if not text or len(text) <= soft_max:
        return [text] if text else []
    # «…antes y después: lista, lista, lista.»
    colon = text.find(": ")
    if colon >= 40 and len(text) - colon >= 40:
        left = text[:colon].rstrip()
        right = _capitalize_sentence_start(text[colon + 2 :])
        if not left.endswith((".", "!", "?", "…")):
            left = f"{left}."
        return [left, right]
    # «…; …»
    if "; " in text:
        parts = [p.strip() for p in text.split("; ") if p.strip()]
        if len(parts) >= 2 and len(parts[0]) >= 40 and len(parts[1]) >= 30:
            head = parts[0]
            if not head.endswith((".", "!", "?", "…")):
                head = f"{head}."
            tail = _capitalize_sentence_start("; ".join(parts[1:]))
            return [head, tail]
    # Mitad cercana a coma (evitar cortar tras artículo corto).
    mid = len(text) // 2
    window_lo = max(40, mid - 90)
    window_hi = min(len(text) - 40, mid + 90)
    best = -1
    for i in range(window_lo, window_hi):
        if text[i : i + 2] != ", ":
            continue
        after = text[i + 2 : i + 12]
        # No dejar «y con …» solo en la 2ª si la 1ª queda sin producto/puente.
        if after.lower().startswith(("y ", "o ", "u ")):
            continue
        best = i
        break
    if best > 0:
        left = text[:best].rstrip()
        right = _capitalize_sentence_start(text[best + 2 :])
        if not left.endswith((".", "!", "?", "…")):
            left = f"{left}."
        if len(left) >= 35 and len(right) >= 35:
            return [left, right]
    return [text]


def _split_long_explanation_sentences(text: str, *, soft_max: int = _WA_LONG_BUBBLE_SOFT) -> list[str]:
    """
    Parte choclos de valor/explicación en 2–3 burbujas por oración completa.
    No corta mid-phrase: solo tras . ! ? … (o : / ; en oraciones únicas largas).
    """
    raw = (text or "").strip()
    if not raw or len(raw) <= soft_max:
        return [raw] if raw else []
    if _is_opening_keep_separate(raw):
        return [raw]
    sents = [s.strip() for s in _WA_SENTENCE_SPLIT_RE.split(raw) if s and s.strip()]
    if len(sents) <= 1:
        return _split_single_long_clause(raw, soft_max=soft_max)
    # Empaquetar oraciones hasta soft_max; CTA (¿…?) casi siempre sola al final.
    packs: list[str] = []
    buf = ""
    for sent in sents:
        is_q = sent.startswith(("¿", "?")) or sent.endswith("?")
        if is_q and buf:
            packs.append(buf.strip())
            buf = sent
            continue
        candidate = f"{buf} {sent}".strip() if buf else sent
        if buf and len(candidate) > soft_max:
            packs.append(buf.strip())
            buf = sent
        else:
            buf = candidate
    if buf:
        packs.append(buf.strip())
    # Si quedó 1 pack enorme, forzar corte por oración (sin vaciar sentido).
    if len(packs) == 1 and len(sents) >= 2:
        mid = max(1, len(sents) // 2)
        packs = [
            " ".join(sents[:mid]).strip(),
            " ".join(sents[mid:]).strip(),
        ]
    # Packs individuales que siguen siendo choclo → cláusula.
    refined: list[str] = []
    for pack in packs:
        if len(pack) > soft_max:
            refined.extend(_split_single_long_clause(pack, soft_max=soft_max))
        else:
            refined.append(pack)
    return [p for p in refined if p]


_SOY_PRESENTATION_HEAD_RE = re.compile(
    r"(?is)^(Soy\s+\S[\wÁÉÍÓÚÑáéíóúñ'’.-]*(?:\s+de\s+[^.!?\n]{1,80})?\.)\s+(.+)$"
)


def _expand_whatsapp_human_bubble(part: str) -> list[str]:
    """Parte afirmación + resto si vinieron juntos; y choclos de explicación."""
    text = (part or "").strip()
    if not text:
        return []
    affirm = _WA_AFFIRM_HEAD_RE.match(text)
    if affirm:
        head = text[: affirm.end()].strip()
        rest = text[affirm.end() :].strip()
        # No partir a mitad de oración (resto en minúscula = continúa la frase).
        if rest and rest[0].islower() and not rest.startswith(("¿", "¡")):
            return _split_long_explanation_sentences(text)
        if rest and len(rest) > 8:
            return [head, *_split_long_explanation_sentences(rest)]
    # «Soy X de Y.» sola; el valor / ancla va en otra burbuja.
    soy = _SOY_PRESENTATION_HEAD_RE.match(text)
    if soy:
        return [
            soy.group(1).strip(),
            *_split_long_explanation_sentences(soy.group(2).strip()),
        ]
    return _split_long_explanation_sentences(text)


def _is_opening_keep_separate(text: str) -> bool:
    return bool(_WA_OPENING_KEEP_SEPARATE_RE.match((text or "").strip()))


def _bubble_edge_is_broken(prev_s: str, nxt_s: str) -> bool:
    """True si hay que reunir: corte mid-phrase / cola colgante («Genial la»)."""
    if not prev_s or not nxt_s:
        return False
    if prev_s.endswith((".", "!", "?", "…", ":", ";")):
        return False
    dangling_m = _WA_DANGLING_TAIL_RE.search(prev_s)
    # Exigir minúsculas reales («la»), no stubs «A»/«B» del split de prueba.
    dangling = bool(dangling_m) and dangling_m.group(1).islower()
    continues_lower = nxt_s[0].islower() and not nxt_s.startswith(("¿", "¡"))
    if dangling or continues_lower:
        return True
    # Fragmento corto tipo «Genial la» sin artículo detectado; no tocar «Hola Mia,» ni «A».
    if prev_s.endswith(","):
        return False
    words = prev_s.split()
    if len(words) <= 3 and any(c.islower() for c in prev_s):
        return True
    return False


def _merge_broken_bubble_edges(parts: list[str]) -> list[str]:
    """Reúne cortes rotos hasta estabilizar (Genial la + Consulta…)."""
    if len(parts) <= 1:
        return parts
    out: list[str] = [parts[0]]
    for nxt in parts[1:]:
        prev = out[-1]
        prev_s = prev.rstrip()
        nxt_s = (nxt or "").strip()
        if not nxt_s:
            continue
        if _bubble_edge_is_broken(prev_s, nxt_s):
            out[-1] = f"{prev_s} {nxt_s}".strip()
        else:
            out.append(nxt_s)
    # Segunda pasada por si quedaron cadenas de 3+ fragmentos.
    if len(out) > 1:
        stable: list[str] = [out[0]]
        for nxt in out[1:]:
            prev_s = stable[-1].rstrip()
            nxt_s = nxt.strip()
            if _bubble_edge_is_broken(prev_s, nxt_s):
                stable[-1] = f"{prev_s} {nxt_s}".strip()
            else:
                stable.append(nxt_s)
        return stable
    return out


def split_whatsapp_default_bubbles(
    text: str,
    *,
    max_bubbles: int | None = None,
) -> list[str]:
    """
    Burbujas humanas: cortar en párrafos / fin de frase; nunca mid-phrase.
    Cold: Hola | Soy X de Y | ¿cómo estás? van SEPARADAS (no un choclo de apertura).
    """
    raw = (text or "").strip()
    if not raw:
        return []
    cap = int(max_bubbles) if max_bubbles is not None else _WA_BUBBLE_MAX
    cap = max(1, min(cap, 8))
    parts = [p.strip() for p in _WA_BUBBLE_SPLIT_RE.split(raw) if p.strip()]
    expanded: list[str] = []
    for part in parts or [raw]:
        expanded.extend(_expand_whatsapp_human_bubble(part))
    expanded = _merge_broken_bubble_edges(expanded)
    if len(expanded) <= 1:
        return [raw] if len(expanded) == 0 else [expanded[0] if expanded else raw]
    deduped: list[str] = []
    for part in expanded:
        if deduped and deduped[-1] == part:
            continue
        deduped.append(part)
    expanded = deduped
    # Último cinturón: si alguna burbuja intermedia no cierra frase, reunir todo.
    expanded = _merge_broken_bubble_edges(expanded)
    # Micro-párrafos de valor: juntar con el siguiente (evita «Te escribo por tu rol.» solo).
    # Nunca fusionar apertura (Hola / Soy… / cómo estás).
    if len(expanded) > 2:
        coalesced: list[str] = [expanded[0]]
        i = 1
        while i < len(expanded):
            cur = expanded[i]
            is_cta = bool(
                re.search(
                    r"(?i)\b(?:agend|llamada|reuni[oó]n|charla|esta semana|la pr[oó]xima)\b",
                    cur,
                )
            )
            if (
                len(cur) < 70
                and not is_cta
                and not _is_opening_keep_separate(cur)
                and i + 1 < len(expanded)
                and not _is_opening_keep_separate(expanded[i + 1])
            ):
                coalesced.append(f"{cur}\n\n{expanded[i + 1]}")
                i += 2
                continue
            coalesced.append(cur)
            i += 1
        expanded = coalesced
    if len(expanded) <= 1:
        return expanded or [raw]
    if len(expanded) > cap:
        expanded = [*expanded[: cap - 1], "\n\n".join(expanded[cap - 1 :])]
    return expanded


_SOY_BARE_RE = re.compile(
    r"(?i)\bSoy\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][\wÁÉÍÓÚÜÑáéíóúüñ'’-]{1,40})\s*\.(?!\s*de\b)"
)
_SOY_WITH_BRAND_RE = re.compile(
    r"(?i)\bSoy\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][\wÁÉÍÓÚÜÑáéíóúüñ'’-]{1,40}\s+de\s+\S+"
)


def ensure_sdr_brand_presentation(
    text: str,
    *,
    sdr_first: str,
    brand: str,
) -> str:
    """Obliga «Soy {nombre} de {empresa}.» (regla outbound)."""
    body = (text or "").strip()
    sdr = (sdr_first or "").strip()
    marca = (brand or "").strip()
    if not body or not sdr or not marca:
        return body
    if _SOY_WITH_BRAND_RE.search(body):
        return body
    bare = re.compile(
        rf"(?i)\bSoy\s+{re.escape(sdr)}\s*\.(?!\s*de\b)"
    )
    if bare.search(body):
        return bare.sub(f"Soy {sdr} de {marca}.", body, count=1)
    if _SOY_BARE_RE.search(body):
        return _SOY_BARE_RE.sub(rf"Soy \1 de {marca}.", body, count=1)
    # Sin presentación: insertar tras saludo o al inicio.
    lines = body.split("\n", 1)
    first = lines[0].strip()
    intro = f"Soy {sdr} de {marca}."
    if re.match(r"(?i)^(hola|buen[oa]s|hey)\b", first):
        rest = lines[1].strip() if len(lines) > 1 else ""
        return f"{first}\n\n{intro}" + (f"\n\n{rest}" if rest else "")
    return f"{intro}\n\n{body}"


def _record_sequence_outbound_history(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    text: str,
    whatsapp_message_id: str | None,
) -> OutreachMessage:
    wamid = (whatsapp_message_id or "").strip() or None
    if wamid:
        found = db.scalars(
            select(OutreachMessage).where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.whatsapp_message_id == wamid,
            )
        ).first()
        if found is not None:
            return found
    hist_text = f"{MEOW_OUTBOUND_NOTE}\n{text}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="user",
        message=hist_text,
        channel="whatsapp",
        direction="outbound",
        whatsapp_message_id=wamid,
    )
    db.add(msg)
    db.flush()
    return msg


def meow_sequence_touch_idempotency_key(
    prospect: Prospect, *, day: int, to_digits: str, campaign_id: int | None = None
) -> str:
    """Misma tanda = misma clave (no reenvía). Otra campaña / reinicio explícito = clave nueva."""
    from app.services.commercial_conversation_state import (
        sequence_send_cycle,
        sequence_send_cycle_at,
    )

    digits = "".join(ch for ch in str(to_digits or "") if ch.isdigit())
    cid = int(campaign_id or getattr(prospect, "campaign_id", 0) or 0)
    cycle = sequence_send_cycle(prospect)
    # Stamp del bump: si reinician a n1 tras wipe de OM, no reusar filas Meow viejas.
    stamp = "".join(c for c in sequence_send_cycle_at(prospect) if c.isalnum())[-8:]
    if stamp:
        return f"seq-{int(prospect.id)}-c{cid}-d{int(day)}-{digits}-n{cycle}-t{stamp}"
    return f"seq-{int(prospect.id)}-c{cid}-d{int(day)}-{digits}-n{cycle}"


def _force_fresh_idempotency_key(base_key: str) -> str:
    """Nueva clave cuando Meow tiene el envío viejo pero Nexus ya no (retest / wipe)."""
    return f"{(base_key or 'seq').strip()}-r{uuid.uuid4().hex[:10]}"


def _outreach_for_meow_wamid(
    db: Session, *, prospect_id: int, whatsapp_msg_id: str | None
) -> OutreachMessage | None:
    wamid = (whatsapp_msg_id or "").strip()
    if not wamid:
        return None
    return db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.whatsapp_message_id == wamid,
        )
        .limit(1)
    ).first()


def _can_legitimately_reuse_meow_row(
    db: Session,
    *,
    prospect_id: int,
    meow_row: WhatsappMeowMessage | None,
    expected_body: str,
) -> OutreachMessage | None:
    """
    Reuso OK solo si Meow está sent + mismo body + ya hay OutreachMessage de ESTE prospecto.
    Si wipearon historial Nexus pero quedó la fila Meow → NO reusar (evita fantasma + typing vacío).
    """
    if meow_row is None:
        return None
    if (meow_row.status or "").strip().lower() != "sent":
        return None
    if (meow_row.body or "").strip() != (expected_body or "").strip():
        return None
    return _outreach_for_meow_wamid(
        db, prospect_id=prospect_id, whatsapp_msg_id=meow_row.whatsapp_msg_id
    )


def _digits_to_jid(digits: str) -> str:
    d = "".join(ch for ch in digits if ch.isdigit())
    return f"{d}@s.whatsapp.net"


def seller_connected_meow_session(
    db: Session, *, company_id: int, seller_user_id: int
) -> WhatsappMeowSession | None:
    return db.scalars(
        select(WhatsappMeowSession)
        .where(
            WhatsappMeowSession.company_id == int(company_id),
            WhatsappMeowSession.user_id == int(seller_user_id),
            WhatsappMeowSession.status == "connected",
        )
        .order_by(WhatsappMeowSession.connected_at.desc().nullslast())
    ).first()


def company_connected_meow_session(
    db: Session, *, company_id: int
) -> WhatsappMeowSession | None:
    """Cualquier sesión Meow conectada de la empresa (fallback si el seller no tiene QR)."""
    return db.scalars(
        select(WhatsappMeowSession)
        .where(
            WhatsappMeowSession.company_id == int(company_id),
            WhatsappMeowSession.status == "connected",
        )
        .order_by(WhatsappMeowSession.connected_at.desc().nullslast())
    ).first()


def sequence_whatsapp_touch_uses_native_meow(
    db: Session,
    *,
    campaign: Campaign,
    day: int,
    channel: str,
    prospect: Prospect | None = None,
    user: User | None = None,
) -> bool:
    """True si hay sesión Meow conectada para operar este toque (prioridad sobre Gupshup/Cloud)."""
    _ = day
    if str(channel or "").strip().lower() != "whatsapp":
        return False
    company_id = int(campaign.company_id)
    candidate_ids: list[int] = []
    for raw in (
        campaign.seller_id,
        getattr(prospect, "owner_user_id", None) if prospect is not None else None,
        user.id if user is not None else None,
    ):
        sid = int(raw or 0)
        if sid > 0 and sid not in candidate_ids:
            candidate_ids.append(sid)
    if not candidate_ids:
        return company_connected_meow_session(db, company_id=company_id) is not None
    for sid in candidate_ids:
        if seller_connected_meow_session(db, company_id=company_id, seller_user_id=sid) is not None:
            return True
    return company_connected_meow_session(db, company_id=company_id) is not None


def resolve_meow_seller_for_sequence_touch(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect | None = None,
    user: User | None = None,
) -> tuple[int, WhatsappMeowSession] | None:
    """Vendedor + sesión Meow a usar (mismo orden que uses_native_meow)."""
    company_id = int(campaign.company_id)
    candidate_ids: list[int] = []
    for raw in (
        campaign.seller_id,
        getattr(prospect, "owner_user_id", None) if prospect is not None else None,
        user.id if user is not None else None,
    ):
        sid = int(raw or 0)
        if sid > 0 and sid not in candidate_ids:
            candidate_ids.append(sid)
    for sid in candidate_ids:
        row = seller_connected_meow_session(db, company_id=company_id, seller_user_id=sid)
        if row is not None:
            return sid, row
    row = company_connected_meow_session(db, company_id=company_id)
    if row is not None:
        return int(row.user_id), row
    return None


def _deliver_one_sequence_bubble(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    session: WhatsappMeowSession,
    company: Any,
    to_digits: str,
    gate: dict[str, Any],
    bubble: str,
    idempotency_key: str,
    record_outreach: bool = True,
) -> dict[str, Any]:
    """
    Envía una burbuja Meow. Con record_outreach=False solo entrega (multi-burbuja);
    el historial Nexus se escribe una sola vez con el texto completo del toque.
    """
    from app.services.whatsapp_meow_service import _find_outbound_by_idempotency

    already = _find_outbound_by_idempotency(
        db, session_uuid=session.session_uuid, idempotency_key=idempotency_key
    )
    # Reuso legítimo: mismo texto + OutreachMessage todavía presente para este prospecto.
    # Si wipearon OM (retest) pero Meow conserva la fila → forzar envío real (anti-fantasma).
    existing_om = _can_legitimately_reuse_meow_row(
        db, prospect_id=int(prospect.id), meow_row=already, expected_body=bubble
    )
    if existing_om is not None and already is not None:
        if not record_outreach:
            return {
                "message_id": existing_om.id,
                "whatsapp_message_id": already.whatsapp_msg_id,
                "dispatch_id": 0,
                "reused": True,
            }
        return {
            "message_id": existing_om.id,
            "whatsapp_message_id": already.whatsapp_msg_id,
            "dispatch_id": 0,
            "reused": True,
        }

    effective_key = idempotency_key
    if (
        already is not None
        and (already.status or "").strip().lower() == "sent"
        and (already.body or "").strip() == (bubble or "").strip()
        and existing_om is None
    ):
        effective_key = _force_fresh_idempotency_key(idempotency_key)
        logger.warning(
            "meow ghost reuse blocked prospect_id=%s key=%s → fresh=%s",
            prospect.id,
            idempotency_key,
            effective_key,
        )

    meow_msg = send_text_message(
        db,
        row=session,
        to=_digits_to_jid(to_digits),
        body=bubble,
        idempotency_key=effective_key,
    )
    if meow_msg.status != "sent":
        err = meow_msg.error or "WhatsApp nativo no confirmó el envío."
        from app.services.whatsapp_meow_reconcile import (
            meow_needs_qr_reconnect,
            reconnect_detail_for_engine_error,
        )

        if meow_needs_qr_reconnect(err):
            raise HTTPException(
                status_code=409,
                detail=reconnect_detail_for_engine_error(err),
            )
        raise HTTPException(
            status_code=502,
            detail=err,
        )
    expected_peer = _digits_to_jid(to_digits)
    if _normalize_meow_peer(meow_msg.peer) != _normalize_meow_peer(expected_peer):
        raise HTTPException(
            status_code=502,
            detail="WhatsApp nativo no confirmó el envío al número del prospecto.",
        )

    if not record_outreach:
        return {
            "message_id": None,
            "whatsapp_message_id": meow_msg.whatsapp_msg_id,
            "dispatch_id": 0,
            "reused": False,
        }

    hist_text = f"{MEOW_OUTBOUND_NOTE}\n{bubble}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="user",
        message=hist_text,
        channel="whatsapp",
        direction="outbound",
        whatsapp_message_id=meow_msg.whatsapp_msg_id or idempotency_key,
    )
    db.add(msg)
    db.flush()

    dispatch = WhatsAppDispatch(
        company_id=int(company.id),
        prospect_id=int(prospect.id),
        campaign_id=int(campaign.id),
        outreach_message_id=int(msg.id),
        destination_digits=to_digits,
        dest_country=str(gate.get("country") or "")[:2] or None,
        category="marketing",
        provider="meow",
        whatsapp_message_id=meow_msg.whatsapp_msg_id,
        submit_status="submitted",
        last_event="submitted",
        billed_units=0,
        reserved_units=1,
        estimated_cost_usd=float(gate.get("estimated_cost_usd") or 0),
        dry_run=False,
    )
    db.add(dispatch)
    db.flush()
    return {
        "message_id": msg.id,
        "whatsapp_message_id": meow_msg.whatsapp_msg_id,
        "dispatch_id": dispatch.id,
        "reused": False,
    }


def _deliver_sequence_whatsapp_bubbles(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    day: int,
    session: WhatsappMeowSession,
    company: Any,
    to_digits: str,
    gate: dict[str, Any],
    full_text: str,
    bubbles: list[str],
) -> dict[str, Any]:
    base_key = meow_sequence_touch_idempotency_key(
        prospect, day=day, to_digits=to_digits, campaign_id=campaign.id
    )
    from app.services.whatsapp_humanize import (
        note_person_sent,
        pacing_enabled,
        perform_send_ritual,
        wait_before_other_person,
    )
    from app.services.whatsapp_meow_service import _find_outbound_by_idempotency, set_chat_presence

    last: dict[str, Any] | None = None
    all_reused = True
    to_jid = _digits_to_jid(to_digits)
    wait_before_other_person(int(campaign.seller_id or 0), int(prospect.id))
    set_typing = lambda state: set_chat_presence(row=session, to=to_jid, state=state)

    # Ritual solo si hay envío real. Reuso legítimo → sin «escribiendo…» de 1 min fantasma.
    for idx, bubble in enumerate(bubbles):
        part_key = f"{base_key}-p{idx + 1}"
        already = _find_outbound_by_idempotency(
            db, session_uuid=session.session_uuid, idempotency_key=part_key
        )
        will_reuse = (
            _can_legitimately_reuse_meow_row(
                db,
                prospect_id=int(prospect.id),
                meow_row=already,
                expected_body=bubble,
            )
            is not None
        )
        if not will_reuse and pacing_enabled():
            perform_send_ritual(
                bubble,
                think=idx == 0,
                set_typing=set_typing,
                cold=True,
            )
        out = _deliver_one_sequence_bubble(
            db,
            campaign=campaign,
            prospect=prospect,
            session=session,
            company=company,
            to_digits=to_digits,
            gate=gate,
            bubble=bubble,
            idempotency_key=part_key,
            # Una sola fila OutreachMessage por toque (no 1 por burbuja).
            record_outreach=False,
        )
        if not out.get("reused"):
            all_reused = False
        last = out

    if last is not None and not all_reused:
        note_person_sent(int(campaign.seller_id or 0), int(prospect.id))

    assert last is not None

    # Todo reusado: devolver el OM existente. NUNCA fabricar otro «Mensaje saliente».
    if all_reused:
        existing = _outreach_for_meow_wamid(
            db,
            prospect_id=int(prospect.id),
            whatsapp_msg_id=last.get("whatsapp_message_id"),
        )
        if existing is None and last.get("message_id"):
            existing = db.get(OutreachMessage, int(last["message_id"]))
        if existing is not None:
            logger.info(
                "sequence_touch_whatsapp_meow_bubbles reuse_only prospect_id=%s day=%s "
                "parts=%s outreach_msg=%s (no ghost OM)",
                prospect.id,
                day,
                len(bubbles),
                existing.id,
            )
            return {
                "message_id": existing.id,
                "whatsapp_message_id": last.get("whatsapp_message_id"),
                "dispatch_id": 0,
                "native_meow": True,
                "reused": True,
                "bubble_count": len(bubbles),
            }

    # Historial Nexus: 1 mensaje = 1 toque de secuencia (aunque Meow mande 2–3 burbujas).
    recorded = _record_sequence_outbound_history(
        db,
        prospect=prospect,
        campaign=campaign,
        text=full_text,
        whatsapp_message_id=(last.get("whatsapp_message_id") or base_key),
    )
    dispatch = WhatsAppDispatch(
        company_id=int(company.id),
        prospect_id=int(prospect.id),
        campaign_id=int(campaign.id),
        outreach_message_id=int(recorded.id),
        destination_digits=to_digits,
        dest_country=str(gate.get("country") or "")[:2] or None,
        category="marketing",
        provider="meow",
        whatsapp_message_id=last.get("whatsapp_message_id"),
        submit_status="submitted",
        last_event="submitted",
        billed_units=0,
        reserved_units=1,
        estimated_cost_usd=float(gate.get("estimated_cost_usd") or 0),
        dry_run=False,
    )
    db.add(dispatch)
    db.flush()

    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=full_text,
    )
    if prospect.status in (
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.not_compatible.value,
    ):
        prospect.status = ProspectStatus.contacted.value
        pipeline_sync.sync_pipeline_from_status(prospect)
    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "whatsapp"

    logger.info(
        "sequence_touch_whatsapp_meow_bubbles prospect_id=%s day=%s parts=%s reused=%s outreach_msg=%s",
        prospect.id,
        day,
        len(bubbles),
        all_reused,
        recorded.id,
    )
    return {
        "message_id": recorded.id,
        "whatsapp_message_id": last.get("whatsapp_message_id"),
        "dispatch_id": dispatch.id,
        "native_meow": True,
        "reused": all_reused,
        "bubble_count": len(bubbles),
    }


def deliver_sequence_whatsapp_touch_native(
    db: Session,
    *,
    user: User,
    campaign: Campaign,
    prospect: Prospect,
    day: int,
    body: str,
) -> dict[str, Any]:
    """Envía toque de secuencia por el número Meow conectado del vendedor."""
    resolved = resolve_meow_seller_for_sequence_touch(
        db, campaign=campaign, prospect=prospect, user=user
    )
    if resolved is None:
        raise HTTPException(
            status_code=409,
            detail="No hay número WhatsApp conectado para este vendedor. Conectalo en Integraciones.",
        )
    seller_id, session = resolved
    if int(campaign.seller_id or 0) != seller_id:
        campaign.seller_id = seller_id
        db.flush()

    # Revalidar contra el motor: DB "connected" no alcanza si el store perdió el device JID.
    try:
        from app.services.whatsapp_meow_service import _engine_get_session, _sync_row

        view = _engine_get_session(session)
        _sync_row(session, view)
        db.flush()
        if (session.status or "").strip().lower() != "connected":
            from app.services.whatsapp_meow_reconcile import reconnect_detail_for_engine_error

            raise HTTPException(
                status_code=409,
                detail=reconnect_detail_for_engine_error(
                    session.last_error or "session not connected"
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        from app.services.whatsapp_meow_reconcile import (
            apply_sync_failure,
            meow_needs_qr_reconnect,
            reconnect_detail_for_engine_error,
        )

        if meow_needs_qr_reconnect(exc):
            from app.services.whatsapp_meow_reconcile import mark_session_needs_qr_reconnect

            mark_session_needs_qr_reconnect(session, exc)
            db.flush()
            raise HTTPException(
                status_code=409,
                detail=reconnect_detail_for_engine_error(exc),
            ) from exc
        apply_sync_failure(session, exc)
        db.flush()

    if int(user.id) != seller_id and int(prospect.owner_user_id or 0) != int(user.id):
        raise HTTPException(
            status_code=403,
            detail="Solo el vendedor de la campaña puede enviar WhatsApp nativo de la secuencia.",
        )

    from app.services import prospect_opt_out

    if prospect_opt_out.is_suppressed(prospect):
        raise HTTPException(status_code=409, detail="Prospecto en lista negra / opt-out; no se envía WhatsApp.")

    from app.services import daily_send_limits as dsl
    from app.services.whatsapp_meow_commercial_isolation import first_nexus_whatsapp_outbound_at

    seller_id = int(campaign.seller_id or 0)
    # Frío = primer WA a este prospecto (no el índice de día del playbook).
    is_cold = first_nexus_whatsapp_outbound_at(db, int(prospect.id)) is None
    if seller_id and is_cold:
        if dsl.whatsapp_monthly_cap_reached(db, seller_id):
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Tope mensual de WhatsApp alcanzado ({dsl.monthly_whatsapp_unanswered_limit()} "
                    "mensajes a personas que todavía no respondieron). Se retoma el mes que viene "
                    "o cuando respondan."
                ),
            )
        if not dsl.can_send(db, seller_id, dsl.KIND_WHATSAPP, cold=True):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Tope de contacto en frío WhatsApp (45 personas/día). "
                    "Las charlas que ya respondieron no cuentan. Se retoma mañana."
                ),
            )
    elif seller_id and dsl.remaining_whatsapp_followups(db, seller_id) <= 0:
        raise HTTPException(
            status_code=429,
            detail="Tope de follow-ups WhatsApp (200/día). Se retoma mañana.",
        )

    to_digits = prospect_whatsapp_digits(prospect)
    if not to_digits:
        raise HTTPException(status_code=400, detail="Prospecto sin teléfono WhatsApp.")

    from app.services.whatsapp_humanize import prepare_outbound_text, perform_send_ritual, pacing_enabled
    from app.services.whatsapp_meow_service import set_chat_presence
    from app.services.campaign_outreach_context import company_brand_name

    text = prepare_outbound_text((body or "").strip(), prospect=prospect, cold=True)
    if not text:
        raise HTTPException(status_code=400, detail="Mensaje vacío.")

    from app.models.company import Company

    company = db.get(Company, campaign.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    brand = company_brand_name(campaign) or (getattr(company, "name", None) or "").strip()
    sdr_first = (
        (getattr(user, "first_name", None) or "").strip()
        or ((getattr(user, "name", None) or "").strip().split() or [""])[0]
    )
    text = ensure_sdr_brand_presentation(text, sdr_first=sdr_first, brand=brand)
    if not text:
        raise HTTPException(status_code=400, detail="Mensaje vacío.")
    try:
        from app.services.outreach_display_names import scrub_piped_product_title_in_body

        prod = getattr(campaign, "product", None)
        pname = (getattr(prod, "name", None) or "").strip()
        if pname:
            text = scrub_piped_product_title_in_body(text, product_name=pname)
    except Exception:
        pass

    try:
        gate = assert_can_send_whatsapp_meow(db, company, digits=to_digits)
    except WhatsAppCommercialError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.detail) from exc

    bubbles = split_whatsapp_default_bubbles(text)
    if len(bubbles) > 1:
        return _deliver_sequence_whatsapp_bubbles(
            db,
            campaign=campaign,
            prospect=prospect,
            day=day,
            session=session,
            company=company,
            to_digits=to_digits,
            gate=gate,
            full_text=text,
            bubbles=bubbles,
        )

    idempotency_key = meow_sequence_touch_idempotency_key(
        prospect, day=day, to_digits=to_digits, campaign_id=campaign.id
    )
    from app.services.whatsapp_meow_service import _find_outbound_by_idempotency

    already = _find_outbound_by_idempotency(
        db, session_uuid=session.session_uuid, idempotency_key=idempotency_key
    )
    existing_om = _can_legitimately_reuse_meow_row(
        db, prospect_id=int(prospect.id), meow_row=already, expected_body=text
    )
    if existing_om is not None and already is not None:
        logger.info(
            "sequence_touch_whatsapp_meow reuse prospect_id=%s day=%s wa_msg_id=%s outreach=%s",
            prospect.id,
            day,
            (already.whatsapp_msg_id or "")[:24],
            existing_om.id,
        )
        return {
            "message_id": existing_om.id,
            "whatsapp_message_id": already.whatsapp_msg_id,
            "dispatch_id": 0,
            "native_meow": True,
            "reused": True,
        }

    effective_key = idempotency_key
    if (
        already is not None
        and (already.status or "").strip().lower() == "sent"
        and (already.body or "").strip() == (text or "").strip()
        and existing_om is None
    ):
        effective_key = _force_fresh_idempotency_key(idempotency_key)
        logger.warning(
            "meow ghost reuse blocked (single) prospect_id=%s key=%s → fresh=%s",
            prospect.id,
            idempotency_key,
            effective_key,
        )

    to_jid = _digits_to_jid(to_digits)
    from app.services.whatsapp_humanize import note_person_sent, wait_before_other_person

    wait_before_other_person(int(campaign.seller_id or 0), int(prospect.id))
    if pacing_enabled():
        perform_send_ritual(
            text,
            set_typing=lambda state: set_chat_presence(row=session, to=to_jid, state=state),
        )
    meow_msg = send_text_message(
        db,
        row=session,
        to=to_jid,
        body=text,
        idempotency_key=effective_key,
    )
    if meow_msg.status != "sent":
        err = meow_msg.error or "WhatsApp nativo no confirmó el envío."
        from app.services.whatsapp_meow_reconcile import (
            meow_needs_qr_reconnect,
            reconnect_detail_for_engine_error,
        )

        if meow_needs_qr_reconnect(err):
            raise HTTPException(
                status_code=409,
                detail=reconnect_detail_for_engine_error(err),
            )
        raise HTTPException(
            status_code=502,
            detail=err,
        )
    expected_peer = _digits_to_jid(to_digits)
    if _normalize_meow_peer(meow_msg.peer) != _normalize_meow_peer(expected_peer):
        raise HTTPException(
            status_code=502,
            detail="WhatsApp nativo no confirmó el envío al número del prospecto.",
        )

    hist_text = f"{MEOW_OUTBOUND_NOTE}\n{text}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="user",
        message=hist_text,
        channel="whatsapp",
        direction="outbound",
        whatsapp_message_id=meow_msg.whatsapp_msg_id or idempotency_key,
    )
    db.add(msg)
    db.flush()
    note_person_sent(int(campaign.seller_id or 0), int(prospect.id))

    dispatch = WhatsAppDispatch(
        company_id=int(company.id),
        prospect_id=int(prospect.id),
        campaign_id=int(campaign.id),
        outreach_message_id=int(msg.id),
        destination_digits=to_digits,
        dest_country=str(gate.get("country") or "")[:2] or None,
        category="marketing",
        provider="meow",
        whatsapp_message_id=meow_msg.whatsapp_msg_id,
        submit_status="submitted",
        last_event="submitted",
        billed_units=0,
        reserved_units=1,
        estimated_cost_usd=float(gate.get("estimated_cost_usd") or 0),
        dry_run=False,
    )
    db.add(dispatch)
    db.flush()

    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=text,
    )
    if prospect.status in (
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.not_compatible.value,
    ):
        prospect.status = ProspectStatus.contacted.value
        pipeline_sync.sync_pipeline_from_status(prospect)
    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "whatsapp"

    logger.info(
        "sequence_touch_whatsapp_meow prospect_id=%s day=%s wa_msg_id=%s",
        prospect.id,
        day,
        (meow_msg.whatsapp_msg_id or "")[:24],
    )
    return {
        "message_id": msg.id,
        "whatsapp_message_id": meow_msg.whatsapp_msg_id,
        "dispatch_id": dispatch.id,
        "native_meow": True,
    }
