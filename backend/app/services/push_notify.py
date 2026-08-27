"""Web Push para Soporte (Sales) y Nexus Support."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.push_subscription import PushSubscription
from app.models.support_ticket import SupportThread
from app.models.user import User
from app.services.support import is_nexus_support_ops

_logger = logging.getLogger("nexus.push")

_VAPID_PATH = Path(__file__).resolve().parents[2] / "data" / "vapid.json"


def _load_or_create_vapid() -> tuple[str, str] | None:
    pub = (os.getenv("VAPID_PUBLIC_KEY") or "").strip()
    priv = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
    if pub and priv:
        return pub, priv
    try:
        if _VAPID_PATH.is_file():
            data = json.loads(_VAPID_PATH.read_text(encoding="utf-8"))
            p, s = (data.get("public_key") or "").strip(), (data.get("private_key") or "").strip()
            if p and s:
                return p, s
    except Exception:
        _logger.warning("[push] no se pudo leer %s", _VAPID_PATH)
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
        from py_vapid.utils import b64urlencode

        vapid = Vapid()
        vapid.generate_keys()
        private_pem = vapid.private_pem().decode("utf-8")
        public_b64 = b64urlencode(
            vapid.public_key.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        )
        _VAPID_PATH.parent.mkdir(parents=True, exist_ok=True)
        _VAPID_PATH.write_text(
            json.dumps({"public_key": public_b64, "private_key": private_pem}, indent=2),
            encoding="utf-8",
        )
        return public_b64, private_pem
    except Exception:
        _logger.warning("[push] no se pudieron generar claves VAPID (¿pywebpush instalado?)")
        return None


def vapid_public_key() -> str | None:
    keys = _load_or_create_vapid()
    return keys[0] if keys else None


def upsert_subscription(
    db: Session,
    *,
    user: User,
    app: str,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> PushSubscription:
    app_key = (app or "").strip().lower()
    if app_key not in {"sales", "support"}:
        raise ValueError("app inválida")
    ep = (endpoint or "").strip()
    row = db.scalars(select(PushSubscription).where(PushSubscription.endpoint == ep)).first()
    if row is None:
        row = PushSubscription(
            user_id=int(user.id),
            app=app_key,
            endpoint=ep,
            p256dh=(p256dh or "").strip(),
            auth=(auth or "").strip(),
        )
        db.add(row)
    else:
        row.user_id = int(user.id)
        row.app = app_key
        row.p256dh = (p256dh or "").strip()
        row.auth = (auth or "").strip()
    db.flush()
    return row


def delete_subscription(db: Session, *, endpoint: str) -> bool:
    row = db.scalars(
        select(PushSubscription).where(PushSubscription.endpoint == (endpoint or "").strip())
    ).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def _ops_user_ids(db: Session) -> list[int]:
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()
    return [int(u.id) for u in users if is_nexus_support_ops(u)]


def _company_user_ids(db: Session, company_id: int) -> list[int]:
    return [
        int(u.id)
        for u in db.scalars(
            select(User).where(User.company_id == int(company_id), User.is_active.is_(True))
        ).all()
    ]


def send_push_to_users(
    db: Session,
    *,
    user_ids: list[int],
    app: str,
    title: str,
    body: str,
    url: str,
) -> int:
    keys = _load_or_create_vapid()
    if not keys:
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        _logger.warning("[push] pywebpush no instalado")
        return 0

    pub, priv = keys
    sub_email = (os.getenv("VAPID_SUBJECT") or os.getenv("NEXUS_SUPPORT_OPS_EMAILS") or "mailto:ops@nexus.local").split(",")[0].strip()
    if not sub_email.startswith("mailto:"):
        sub_email = f"mailto:{sub_email}"

    rows = list(
        db.scalars(
            select(PushSubscription).where(
                PushSubscription.user_id.in_([int(i) for i in user_ids]),
                PushSubscription.app == app,
            )
        ).all()
    )
    sent = 0
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
    stale: list[PushSubscription] = []
    for row in rows:
        try:
            webpush(
                subscription_info={
                    "endpoint": row.endpoint,
                    "keys": {"p256dh": row.p256dh, "auth": row.auth},
                },
                data=payload,
                vapid_private_key=priv,
                vapid_claims={"sub": sub_email},
                vapid_public_key=pub,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                stale.append(row)
            else:
                _logger.info("[push] fallo endpoint=%s status=%s", row.endpoint[:48], status)
        except Exception:
            _logger.exception("[push] error enviando a user_id=%s", row.user_id)
    for row in stale:
        db.delete(row)
    if stale:
        db.flush()
    return sent


def notify_support_message(db: Session, *, thread: SupportThread, role: str, preview: str) -> None:
    company = getattr(thread, "company", None)
    company_name = getattr(company, "name", None) or f"Empresa {thread.company_id}"
    text = (preview or "").strip()[:180] or "Nuevo mensaje"
    if role == "user":
        opener = getattr(thread, "opened_by", None)
        who = (
            (getattr(opener, "name", None) or getattr(opener, "email", None) or "Un usuario")
            .strip()
        )
        send_push_to_users(
            db,
            user_ids=_ops_user_ids(db),
            app="support",
            title=f"{who} ({company_name}) escribió en Soporte",
            body=text,
            url="/",
        )
        return
    if role == "support":
        send_push_to_users(
            db,
            user_ids=[int(thread.opened_by_user_id)],
            app="sales",
            title="Nexus Support te respondió",
            body=text,
            url="/soporte",
        )
