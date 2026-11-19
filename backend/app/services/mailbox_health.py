"""Salud de la casilla Gmail de cada SDR.

- Rebotes: busca avisos de mailer-daemon en la casilla y los asocia al prospecto.
- Freno: si rebota más del 3 % en 24 h o caen fuerte las respuestas, baja el cupo
  diario (nivel 1 = mitad, 2 = cuarto, 3 = décima parte) y avisa en la actividad.
  Se recupera de a un nivel cada 2 días sin problemas.
- Historial previo: cuántos mails por día mandaba la casilla antes de Nexus
  (arranca el cupo gradual desde ahí).
- Dominio: revisa SPF, DKIM y DMARC del dominio del remitente.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.models.mailbox_health import EmailBounce, MailboxHealth
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services import daily_send_limits as dsl

logger = logging.getLogger(__name__)

BOUNCE_RATE_LIMIT = 0.03
BOUNCE_MIN_SENT = 20
BOUNCE_ABS_LIMIT = 5
REPLY_DROP_RATIO = 0.4
REPLY_MIN_COHORT = 40
BRAKE_STEP_UP_HOURS = 20
BRAKE_RECOVER_HOURS = 48
EVALUATE_EVERY = timedelta(hours=1)
DOMAIN_CHECK_EVERY = timedelta(hours=24)

PERSONAL_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def get_or_create(db: Session, seller_id: int, company_id: int | None = None) -> MailboxHealth:
    row = db.scalars(select(MailboxHealth).where(MailboxHealth.seller_id == seller_id)).first()
    if row is None:
        row = MailboxHealth(seller_id=seller_id, company_id=company_id, brake_level=0)
        db.add(row)
        db.flush()
    elif company_id and not row.company_id:
        row.company_id = company_id
    return row


def _seller_campaigns(db: Session, seller_id: int) -> list[Campaign]:
    return list(db.scalars(select(Campaign).where(Campaign.seller_id == seller_id)).all())


def _notify_seller(db: Session, seller_id: int, line: str) -> None:
    """Aviso en la actividad (canal email) de las campañas activas del SDR."""
    from app.services import multichannel_sequence as mseq

    for c in _seller_campaigns(db, seller_id):
        if (c.status or "") not in ("ready", "running"):
            continue
        mseq._append_log(c, line, kind="warning", channel="email")


# ——— Gmail desconectado ———

GMAIL_ALERT_REPEAT = timedelta(hours=24)


def is_gmail_disconnect_error(exc: Exception) -> bool:
    """Google revocó el acceso o no quedan tokens. Un corte de red o un 5xx no cuenta."""
    low = str(getattr(exc, "detail", None) or exc).lower()
    return "invalid_grant" in low or "reconect" in low or "no está conectado" in low


def _active_campaigns(db: Session, seller_id: int) -> list[Campaign]:
    return [c for c in _seller_campaigns(db, seller_id) if (c.status or "") in ("ready", "running")]


def _send_gmail_down_alert(db: Session, seller_id: int, gmail_email: str | None) -> None:
    from app.models.user import User
    from app.services.push_notify import send_push_to_users
    from app.services.system_email import send_system_email

    user = db.get(User, seller_id)
    if user is None:
        return
    base = (os.getenv("NEXUS_FRONTEND_URL") or "http://127.0.0.1:5173").strip().rstrip("/")
    link = f"{base}/integraciones"
    casilla = f" ({gmail_email})" if gmail_email else ""
    name = (user.first_name or user.name or "").strip() or "Hola"
    body = (
        f"{name},\n\n"
        f"Google cortó la conexión de tu Gmail{casilla} con Nexus. "
        "Mientras siga así, Nexus no puede mandar los mails de tus secuencias ni leer las respuestas.\n\n"
        f"Para arreglarlo, entrá a Integraciones y volvé a conectar Google:\n{link}\n\n"
        "Tarda un minuto. Apenas lo reconectes, los mails siguen solos desde donde quedaron.\n\n"
        "— Nexus Sales\n"
    )
    if user.email:
        try:
            send_system_email(to=user.email, subject="Tu Gmail se desconectó de Nexus", text_body=body)
        except Exception:
            logger.exception("gmail down email seller=%s", seller_id)
    try:
        send_push_to_users(
            db,
            user_ids=[seller_id],
            app="sales",
            title="Tu Gmail se desconectó de Nexus",
            body="Los mails de tus secuencias están frenados. Reconectá Google en Integraciones.",
            url="/integraciones",
        )
    except Exception:
        logger.exception("gmail down push seller=%s", seller_id)


def note_gmail_down(db: Session, health: MailboxHealth, gmail_email: str | None, *, now: datetime) -> bool:
    """Marca la casilla caída y avisa (una vez, y un recordatorio cada 24 h). True si avisó."""
    if health.gmail_down_since is None:
        health.gmail_down_since = now
    last = _aware(health.gmail_alert_at)
    if last is not None and now - last < GMAIL_ALERT_REPEAT:
        return False
    if not _active_campaigns(db, int(health.seller_id)):
        return False
    health.gmail_alert_at = now
    _notify_seller(
        db,
        int(health.seller_id),
        "Gmail desconectado: Google cortó el acceso y los mails están frenados. "
        "Reconectá Google en Integraciones y siguen solos.",
    )
    _send_gmail_down_alert(db, int(health.seller_id), gmail_email)
    return True


def note_gmail_back(db: Session, health: MailboxHealth) -> bool:
    if health.gmail_down_since is None:
        return False
    health.gmail_down_since = None
    health.gmail_alert_at = None
    _notify_seller(db, int(health.seller_id), "Gmail reconectado: los mails de la secuencia siguen.")
    return True


# ——— Rebotes ———


def is_temporary_delay(subject: str, body: str) -> bool:
    """Aviso de demora (Gmail sigue reintentando): no es rebote."""
    s = (subject or "").lower()
    if "delay" in s or "demora" in s or "retras" in s:
        return True
    low = (body or "").lower()
    return "will retry" in low or "seguirá intentando" in low or "volverá a intentar" in low


def bounced_recipients(headers: dict[str, str], body: str) -> list[str]:
    raw = headers.get("x-failed-recipients", "")
    found = [e.strip().lower() for e in raw.split(",") if "@" in e]
    if found:
        return found
    return [e.lower() for e in _EMAIL_RE.findall(body or "")]


def _seller_prospect_by_email(db: Session, seller_id: int, emails: list[str]) -> Prospect | None:
    ids = [c.id for c in _seller_campaigns(db, seller_id)]
    if not ids or not emails:
        return None
    for em in emails:
        p = db.scalars(
            select(Prospect).where(
                Prospect.campaign_id.in_(ids),
                func.lower(Prospect.email) == em,
            )
        ).first()
        if p is not None:
            return p
    return None


def record_bounce(
    db: Session,
    *,
    seller_id: int,
    company_id: int | None,
    gmail_message_id: str,
    recipients: list[str],
    bounced_at: datetime | None = None,
) -> EmailBounce | None:
    exists = db.scalars(
        select(EmailBounce).where(EmailBounce.gmail_message_id == gmail_message_id)
    ).first()
    if exists is not None:
        return None
    recipients = [r.strip().lower() for r in recipients if r and r.strip()]
    prospect = _seller_prospect_by_email(db, seller_id, recipients)
    row = EmailBounce(
        seller_id=seller_id,
        company_id=company_id,
        prospect_id=prospect.id if prospect else None,
        email=(prospect.email.strip().lower() if prospect and prospect.email else (recipients[0] if recipients else None)),
        gmail_message_id=gmail_message_id,
        bounced_at=bounced_at or _now(),
    )
    db.add(row)
    db.flush()
    if prospect is not None:
        from app.services import multichannel_sequence as mseq

        campaign = db.get(Campaign, prospect.campaign_id)
        if campaign is not None:
            mseq._append_log(
                campaign,
                f"El mail a {prospect.name or prospect.email} rebotó (la dirección no existe). "
                "No le vamos a mandar más mails a esa dirección.",
                kind="warning",
                channel="email",
            )
    return row


def scan_bounces(db: Session, *, seller_id: int, company_id: int, access: str) -> int:
    """Lee avisos de rebote recientes de la casilla del SDR. Devuelve cuántos nuevos."""
    from app.services.gmail_inbound_sync import _header_map, _is_gmail_delivery_bounce, plain_body_from_gmail_message
    from app.services.gmail_threads import fetch_message_full, gmail_get

    new = 0
    with httpx.Client() as client:
        data = gmail_get(
            client,
            access,
            "/messages",
            params={"q": "from:(mailer-daemon OR postmaster) newer_than:3d", "maxResults": 100},
        )
        if data.get("_unauthorized"):
            return 0
        for m in data.get("messages") or []:
            mid = str(m.get("id") or "")
            if not mid:
                continue
            if db.scalar(select(func.count()).select_from(EmailBounce).where(EmailBounce.gmail_message_id == mid)):
                continue
            full = fetch_message_full(client, access, mid)
            if not full:
                continue
            headers = _header_map(full.get("payload") or {})
            body = plain_body_from_gmail_message(full)
            subject = headers.get("subject", "")
            if not _is_gmail_delivery_bounce(subject=subject, body=body, from_header=headers.get("from", "")):
                continue
            if is_temporary_delay(subject, body):
                continue
            try:
                at = datetime.fromtimestamp(int(full.get("internalDate") or 0) / 1000, tz=UTC)
            except (TypeError, ValueError):
                at = _now()
            if record_bounce(
                db,
                seller_id=seller_id,
                company_id=company_id,
                gmail_message_id=mid,
                recipients=bounced_recipients(headers, body),
                bounced_at=at,
            ):
                new += 1
    return new


def bounces_last_24h(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    now = now or _now()
    n = db.scalar(
        select(func.count())
        .select_from(EmailBounce)
        .where(
            EmailBounce.seller_id == seller_id,
            EmailBounce.prospect_id.isnot(None),
            EmailBounce.bounced_at >= now - timedelta(days=1),
        )
    )
    return int(n or 0)


# ——— Caída de respuestas ———


def _reply_rate(db: Session, seller_id: int, start: datetime, end: datetime) -> tuple[int, float]:
    """(prospectos contactados por primera vez en la ventana, % que respondió por cualquier canal)."""
    campaign_ids = [c.id for c in _seller_campaigns(db, seller_id)]
    if not campaign_ids:
        return 0, 0.0
    first = (
        select(OutreachMessage.prospect_id, func.min(OutreachMessage.created_at).label("first_at"))
        .where(
            OutreachMessage.campaign_id.in_(campaign_ids),
            OutreachMessage.channel == "email",
            OutreachMessage.direction == "outbound",
            OutreachMessage.gmail_message_id.isnot(None),
        )
        .group_by(OutreachMessage.prospect_id)
        .subquery()
    )
    cohort = [
        int(pid)
        for (pid,) in db.execute(
            select(first.c.prospect_id).where(first.c.first_at >= start, first.c.first_at < end)
        ).all()
    ]
    if not cohort:
        return 0, 0.0
    replied = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id))).where(
            OutreachMessage.prospect_id.in_(cohort),
            OutreachMessage.direction == "inbound",
        )
    )
    return len(cohort), int(replied or 0) / len(cohort)


def problems_now(db: Session, seller_id: int, *, now: datetime | None = None) -> str | None:
    """Motivo para frenar (texto para el SDR) o None si la casilla viene bien."""
    now = now or _now()
    sent = dsl.emails_sent_today(db, seller_id, now=now)
    bounced = bounces_last_24h(db, seller_id, now=now)
    if bounced >= BOUNCE_ABS_LIMIT or (sent >= BOUNCE_MIN_SENT and bounced / max(sent, 1) > BOUNCE_RATE_LIMIT):
        pct = round(100 * bounced / max(sent, 1))
        return f"rebotaron {bounced} de {sent} mails en las últimas 24 h ({pct} %)"
    recent_n, recent_rate = _reply_rate(db, seller_id, now - timedelta(days=9), now - timedelta(days=2))
    base_n, base_rate = _reply_rate(db, seller_id, now - timedelta(days=30), now - timedelta(days=9))
    if (
        recent_n >= REPLY_MIN_COHORT
        and base_n >= REPLY_MIN_COHORT
        and base_rate > 0
        and recent_rate < base_rate * REPLY_DROP_RATIO
    ):
        return (
            f"las respuestas bajaron de {round(base_rate * 100, 1)} % a {round(recent_rate * 100, 1)} % "
            "(puede que los mails estén cayendo en spam)"
        )
    return None


def evaluate_brake(db: Session, health: MailboxHealth, *, now: datetime | None = None) -> str | None:
    """Sube o baja el freno. Devuelve 'up' / 'down' si cambió."""
    now = now or _now()
    health.evaluated_at = now
    reason = problems_now(db, int(health.seller_id), now=now)
    level = int(health.brake_level or 0)
    changed_at = _aware(health.brake_changed_at)
    hours_since = (now - changed_at).total_seconds() / 3600 if changed_at else 1e9

    if reason and level < 3 and (level == 0 or hours_since >= BRAKE_STEP_UP_HOURS):
        health.brake_level = level + 1
        health.brake_reason = reason
        health.brake_changed_at = now
        cap = dsl.email_cap_today(db, int(health.seller_id), now=now)["cap"]
        _notify_seller(
            db,
            int(health.seller_id),
            f"Cuidamos tu casilla: {reason}. Bajamos el envío de mails a {cap} por día "
            "hasta que se normalice. Revisá que los contactos tengan emails válidos.",
        )
        return "up"
    if not reason and level > 0 and hours_since >= BRAKE_RECOVER_HOURS:
        health.brake_level = level - 1
        health.brake_changed_at = now
        if health.brake_level == 0:
            health.brake_reason = None
        cap = dsl.email_cap_today(db, int(health.seller_id), now=now)["cap"]
        _notify_seller(
            db,
            int(health.seller_id),
            f"Tu casilla viene bien: subimos el envío de mails a {cap} por día.",
        )
        return "down"
    return None


# ——— Historial previo de la casilla ———


def measure_prior_daily_sent(db: Session, health: MailboxHealth, *, access: str) -> int:
    """Promedio diario de mails enviados en 30 días, sin contar los de Nexus."""
    from app.services.gmail_threads import gmail_get

    total = 0
    page: str | None = None
    with httpx.Client() as client:
        for _ in range(6):
            params: dict[str, Any] = {"q": "in:sent newer_than:30d", "maxResults": 500}
            if page:
                params["pageToken"] = page
            data = gmail_get(client, access, "/messages", params=params)
            if data.get("_unauthorized"):
                break
            total += len(data.get("messages") or [])
            page = data.get("nextPageToken")
            if not page:
                break
    campaign_ids = [c.id for c in _seller_campaigns(db, int(health.seller_id))]
    nexus = 0
    if campaign_ids:
        nexus = int(
            db.scalar(
                select(func.count())
                .select_from(OutreachMessage)
                .where(
                    OutreachMessage.campaign_id.in_(campaign_ids),
                    OutreachMessage.channel == "email",
                    OutreachMessage.direction == "outbound",
                    OutreachMessage.gmail_message_id.isnot(None),
                    OutreachMessage.created_at >= _now() - timedelta(days=30),
                )
            )
            or 0
        )
    daily = max(0, total - nexus) // 30
    health.prior_daily_sent = daily
    health.prior_checked_at = _now()
    return daily


# ——— Dominio: SPF / DKIM / DMARC ———


def _txt_via_doh(name: str) -> list[str] | None:
    res = httpx.get(
        "https://dns.google/resolve", params={"name": name, "type": "TXT"}, timeout=8.0
    )
    res.raise_for_status()
    data = res.json()
    status = int(data.get("Status", -1))
    if status == 3:
        return []
    if status != 0:
        return None
    out: list[str] = []
    for ans in data.get("Answer") or []:
        if int(ans.get("type", 0)) != 16:
            continue
        raw = str(ans.get("data") or "")
        out.append("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', raw)) or raw.strip('"'))
    return out


def _txt_via_dnspython(name: str) -> list[str] | None:
    import dns.resolver

    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=8.0)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    out: list[str] = []
    for r in answers:
        parts = getattr(r, "strings", None) or []
        out.append("".join(p.decode("utf-8", "replace") if isinstance(p, bytes) else str(p) for p in parts))
    return out


def _txt_records(name: str) -> list[str] | None:
    """Registros TXT; None si no se pudo consultar (no es lo mismo que 'no existe')."""
    for lookup in (_txt_via_doh, _txt_via_dnspython):
        try:
            got = lookup(name)
        except Exception:
            continue
        if got is not None:
            return got
    return None


def check_domain(email: str | None) -> dict[str, Any]:
    em = (email or "").strip().lower()
    domain = em.split("@", 1)[1] if "@" in em else ""
    if not domain:
        return {"domain": None, "ok": True, "personal": False, "issues": []}
    if domain in PERSONAL_GMAIL_DOMAINS:
        return {"domain": domain, "ok": True, "personal": True, "issues": []}

    spf_raw = _txt_records(domain)
    dmarc_raw = _txt_records(f"_dmarc.{domain}")
    dkim_raw = _txt_records(f"google._domainkey.{domain}")
    if spf_raw is None or dmarc_raw is None or dkim_raw is None:
        return {"domain": domain, "ok": True, "personal": False, "unknown": True, "issues": []}
    spf = [t for t in spf_raw if t.lower().startswith("v=spf1")]
    dmarc = [t for t in dmarc_raw if t.lower().startswith("v=dmarc1")]
    dkim = [t for t in dkim_raw if "p=" in t.lower()]

    issues: list[dict[str, str]] = []
    if not spf:
        issues.append({
            "key": "spf",
            "title": "Falta SPF",
            "fix": f'En el DNS de {domain}, agregá un registro TXT en "@" con: v=spf1 include:_spf.google.com ~all',
        })
    elif "_spf.google.com" not in spf[0].lower():
        issues.append({
            "key": "spf",
            "title": "El SPF no incluye a Google",
            "fix": f'En el registro TXT SPF de {domain}, sumá "include:_spf.google.com" antes del "~all".',
        })
    if not dkim:
        issues.append({
            "key": "dkim",
            "title": "No encontramos la firma DKIM de Google",
            "fix": (
                "En Google Workspace: Admin → Aplicaciones → Gmail → Autenticar correo electrónico → "
                f"Generar registro nuevo, pegalo como TXT en el DNS de {domain} y tocá \"Iniciar autenticación\". "
                "Si ya lo configuraste con otro nombre de selector, podés ignorar este aviso."
            ),
        })
    if not dmarc:
        issues.append({
            "key": "dmarc",
            "title": "Falta DMARC",
            "fix": f'En el DNS de {domain}, agregá un TXT en "_dmarc" con: v=DMARC1; p=none; rua=mailto:dmarc@{domain}',
        })
    return {
        "domain": domain,
        "ok": not issues,
        "personal": False,
        "spf": bool(spf),
        "dkim": bool(dkim),
        "dmarc": bool(dmarc),
        "issues": issues,
    }


def refresh_domain_check(db: Session, health: MailboxHealth, email: str | None) -> dict[str, Any]:
    before = health.domain_check or {}
    result = check_domain(email)
    if result.get("unknown"):
        # DNS no respondió: reintentar en el próximo tick, sin pisar el último resultado.
        return before or result
    health.domain_check = result
    health.domain_checked_at = _now()
    before_keys = {i.get("key") for i in before.get("issues") or []}
    new_keys = {i["key"] for i in result.get("issues") or []}
    if new_keys - before_keys:
        names = ", ".join(sorted(k.upper() for k in new_keys))
        _notify_seller(
            db,
            int(health.seller_id),
            f"Tu dominio {result.get('domain')} no tiene {names} bien configurado: los mails pueden "
            "caer en spam. En la pestaña Mail te dejamos los pasos para arreglarlo.",
        )
    return result


# ——— Tick ———


def _gmail_accounts(db: Session) -> list[ConnectedAccount]:
    return list(
        db.scalars(
            select(ConnectedAccount).where(
                ConnectedAccount.provider == IntegrationProvider.gmail.value,
                ConnectedAccount.status.in_(
                    [IntegrationStatus.connected.value, IntegrationStatus.error.value]
                ),
            )
        ).all()
    )


def refresh_seller(db: Session, account: ConnectedAccount, *, now: datetime | None = None) -> dict[str, Any]:
    from app.services.gmail_drafts import get_valid_gmail_connection

    now = now or _now()
    seller_id = int(account.user_id)
    health = get_or_create(db, seller_id, int(account.company_id))
    out: dict[str, Any] = {"seller_id": seller_id}

    checked = _aware(health.domain_checked_at)
    if checked is None or now - checked >= DOMAIN_CHECK_EVERY:
        refresh_domain_check(db, health, account.external_email)

    access: str | None = None
    try:
        access, _ = get_valid_gmail_connection(db, company_id=int(account.company_id), user_id=seller_id)
    except Exception as exc:
        access = None
        if is_gmail_disconnect_error(exc):
            out["gmail_alert"] = note_gmail_down(db, health, account.external_email, now=now)
    if access:
        if note_gmail_back(db, health):
            out["gmail_back"] = True
        if health.prior_checked_at is None:
            try:
                out["prior_daily_sent"] = measure_prior_daily_sent(db, health, access=access)
            except Exception:
                logger.exception("mailbox prior volume seller=%s", seller_id)
                health.prior_checked_at = now
        try:
            out["new_bounces"] = scan_bounces(db, seller_id=seller_id, company_id=int(account.company_id), access=access)
            health.bounce_scan_at = now
        except Exception:
            logger.exception("mailbox bounce scan seller=%s", seller_id)

    evaluated = _aware(health.evaluated_at)
    if out.get("new_bounces") or evaluated is None or now - evaluated >= EVALUATE_EVERY:
        out["brake"] = evaluate_brake(db, health, now=now)
    return out


def run_mailbox_health(db: Session) -> dict[str, Any]:
    done = 0
    for account in _gmail_accounts(db):
        has_campaign = db.scalar(
            select(func.count()).select_from(Campaign).where(Campaign.seller_id == account.user_id)
        )
        if not has_campaign:
            continue
        try:
            refresh_seller(db, account)
            db.commit()
            done += 1
        except Exception:
            db.rollback()
            logger.exception("mailbox health seller=%s", account.user_id)
    return {"sellers": done, "finished_at": _now().isoformat()}


def mailbox_status(db: Session, seller_id: int) -> dict[str, Any]:
    """Estado de la casilla para la pestaña Mail."""
    info = dsl.email_cap_today(db, seller_id)
    health = db.scalars(select(MailboxHealth).where(MailboxHealth.seller_id == seller_id)).first()
    gap = dsl.email_gap_seconds(db, seller_id)
    return {
        "cap_today": int(info["cap"]),
        "base_limit": int(info["base"]),
        "sent_24h": dsl.emails_sent_today(db, seller_id),
        "ramping": bool(info["ramping"]),
        "ramp_day": info["ramp_day"],
        "brake_level": int(info["brake_level"]),
        "brake_reason": info["brake_reason"],
        "bounces_24h": bounces_last_24h(db, seller_id),
        "gap_minutes": round(gap / 60, 1),
        "domain": (health.domain_check if health is not None else None) or None,
        "gmail_disconnected": bool(health is not None and health.gmail_down_since is not None),
    }


def refresh_after_gmail_connect(company_id: int, user_id: int) -> None:
    """Al conectar Gmail: revisar dominio y medir historial previo (en segundo plano)."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        account = db.scalars(
            select(ConnectedAccount).where(
                ConnectedAccount.company_id == company_id,
                ConnectedAccount.user_id == user_id,
                ConnectedAccount.provider == IntegrationProvider.gmail.value,
            )
        ).first()
        if account is None:
            return
        health = get_or_create(db, user_id, company_id)
        refresh_domain_check(db, health, account.external_email)
        db.commit()
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        refresh_seller(db, account)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("mailbox health on connect user=%s", user_id)
    finally:
        db.close()
