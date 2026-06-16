"""
Contrato de estados de integración (catálogo).

El valor persistido vive en `ConnectedAccount.status` como string alineado a
`IntegrationStatus` en `app.models.enums`.
"""

from __future__ import annotations

from app.models.enums import IntegrationProvider, IntegrationStatus

__all__ = ["IntegrationProvider", "IntegrationStatus"]
