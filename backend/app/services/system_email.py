"""Email transaccional de Nexus (SMTP). Olvidé contraseña, etc."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    host = (os.getenv("NEXUS_SMTP_HOST") or "").strip()
    user = (os.getenv("NEXUS_SMTP_USER") or "").strip()
    password = (os.getenv("NEXUS_SMTP_PASSWORD") or "").strip()
    return bool(host and user and password)


def _from_header() -> str:
    raw = (os.getenv("NEXUS_SMTP_FROM") or "").strip()
    if raw:
        return raw
    user = (os.getenv("NEXUS_SMTP_USER") or "").strip()
    return f"Nexus Sales <{user}>" if user else "Nexus Sales <noreply@localhost>"


def send_system_email(*, to: str, subject: str, text_body: str) -> bool:
    """Envía un mail plano. False si SMTP no está configurado o falla el envío."""
    if not smtp_configured():
        logger.warning("[system-email] SMTP no configurado; no se envió a %s (%s)", to, subject)
        return False
    host = (os.getenv("NEXUS_SMTP_HOST") or "").strip()
    port = int((os.getenv("NEXUS_SMTP_PORT") or "587").strip() or "587")
    user = (os.getenv("NEXUS_SMTP_USER") or "").strip()
    password = (os.getenv("NEXUS_SMTP_PASSWORD") or "").strip()
    use_tls = (os.getenv("NEXUS_SMTP_TLS") or "1").strip().lower() in ("1", "true", "yes", "on")

    msg = EmailMessage()
    msg["From"] = _from_header()
    msg["To"] = to.strip()
    msg["Subject"] = subject
    msg.set_content(text_body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
        logger.info("[system-email] enviado a %s (%s)", to, subject)
        return True
    except Exception:
        logger.exception("[system-email] fallo enviando a %s", to)
        return False


def send_password_reset_code(*, to: str, code: str, name: str | None = None) -> bool:
    greeting = (name or "").strip() or "Hola"
    body = (
        f"{greeting},\n\n"
        f"Tu código para cambiar la contraseña de Nexus Sales es:\n\n"
        f"    {code}\n\n"
        f"Vence en 15 minutos. Si no pediste este cambio, ignorá este mail.\n\n"
        f"— Nexus Sales\n"
    )
    return send_system_email(
        to=to,
        subject="Código para cambiar tu contraseña — Nexus Sales",
        text_body=body,
    )
