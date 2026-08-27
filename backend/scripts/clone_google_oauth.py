"""Copia tokens Google (Gmail+Calendar) de un usuario donante a otro de la misma empresa.

Uso típico demo: el refresh del SDR quedó invalid_grant y la UI es solo lectura.
  python scripts/clone_google_oauth.py --from director@test.com --to sdr@test.com
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.models.user import User

GOOGLE_PROVIDERS = (
    IntegrationProvider.gmail.value,
    IntegrationProvider.google_calendar.value,
)


def _user_by_email(db, email: str) -> User:
    user = db.scalars(select(User).where(User.email == email)).first()
    if user is None:
        raise SystemExit(f"Usuario no encontrado: {email}")
    return user


def _row(db, company_id: int, user_id: int, provider: str) -> ConnectedAccount | None:
    return db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == provider,
        )
    ).first()


def clone(db, *, donor: User, recipient: User) -> int:
    if donor.company_id != recipient.company_id:
        raise SystemExit("Donante y destinatario deben ser de la misma empresa.")
    n = 0
    for provider in GOOGLE_PROVIDERS:
        src = _row(db, donor.company_id, donor.id, provider)
        if src is None or not src.refresh_token_encrypted:
            print(f"skip {provider}: donante sin fila/refresh")
            continue
        dst = _row(db, recipient.company_id, recipient.id, provider)
        if dst is None:
            dst = ConnectedAccount(
                company_id=recipient.company_id,
                user_id=recipient.id,
                provider=provider,
            )
            db.add(dst)
        dst.status = IntegrationStatus.connected.value
        dst.external_email = src.external_email
        dst.access_token_encrypted = src.access_token_encrypted
        dst.refresh_token_encrypted = src.refresh_token_encrypted
        dst.connected_at = src.connected_at
        n += 1
        print(f"cloned {provider} -> user {recipient.id} ({src.external_email})")
    db.commit()
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_email", required=True)
    parser.add_argument("--to", dest="to_email", required=True)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        donor = _user_by_email(db, args.from_email)
        recipient = _user_by_email(db, args.to_email)
        print(f"from={donor.email}(id={donor.id}) to={recipient.email}(id={recipient.id})")
        print(f"cloned={clone(db, donor=donor, recipient=recipient)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
