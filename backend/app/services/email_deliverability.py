"""Validación de emails aptos para envío real por Gmail (no demo / no simulación)."""

from __future__ import annotations

import os
import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_BLOCKED_DOMAINS = (
    "mail.nexus-sales.local",
    "nexus-sales.local",
    "example.com",
    "example.org",
    "test.com",
    "localhost",
)

_BLOCKED_LOCAL_PREFIXES = (
    "demo.prospect.",
    "demo.",
    "test.",
    "fake.",
)

_BLOCKED_TLDS = (
    ".local",
    ".invalid",
    ".test",
    ".example",
    ".localhost",
)


def is_real_deliverable_email(email: str | None) -> bool:
    """True solo si el email puede recibir correo real (no seeds demo ni dominios internos)."""
    return deliverable_email_skip_reason(email) is None


def deliverable_email_skip_reason(email: str | None) -> str | None:
    em = (email or "").strip().lower()
    if not em:
        return "sin email"
    if not _EMAIL_RE.match(em):
        return "formato inválido"
    local, _, domain = em.partition("@")
    if not local or not domain:
        return "formato inválido"
    if domain in _BLOCKED_DOMAINS:
        return f"dominio de prueba ({domain})"
    for tld in _BLOCKED_TLDS:
        if domain.endswith(tld):
            return f"dominio no entregable ({domain})"
    for prefix in _BLOCKED_LOCAL_PREFIXES:
        if local.startswith(prefix):
            return "email demo de simulación"
    if "nexus-sales" in domain:
        return "dominio interno Nexus"
    return None


def real_email_guard_enabled() -> bool:
    """En modo real, por defecto solo emails entregables (salvo override explícito)."""
    override = (os.getenv("NEXUS_ALLOW_DEMO_EMAIL_SEND") or "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return False
    from app.services import outreach_metrics as om

    return om.is_real_mode() or (os.getenv("NEXUS_REAL_EMAIL_ONLY") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def assert_real_deliverable_email(email: str | None) -> None:
    if not real_email_guard_enabled():
        return
    reason = deliverable_email_skip_reason(email)
    if reason:
        raise ValueError(
            f"Email no apto para envío real ({reason}). "
            "Usá un correo real del prospecto o desactivá NEXUS_REAL_EMAIL_ONLY."
        )
