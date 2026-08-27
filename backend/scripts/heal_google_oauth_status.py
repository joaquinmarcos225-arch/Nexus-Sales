"""Heal Gmail/Calendar ConnectedAccount rows stuck in error while tokens remain."""
from __future__ import annotations

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus


def main() -> None:
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ConnectedAccount).where(
                    ConnectedAccount.provider.in_(
                        [
                            IntegrationProvider.gmail.value,
                            IntegrationProvider.google_calendar.value,
                        ]
                    )
                )
            )
        )
        print(f"google rows: {len(rows)}")
        healed = 0
        for r in rows:
            has_tokens = bool(r.access_token_encrypted) or bool(r.refresh_token_encrypted)
            print(
                f"  user={r.user_id} {r.provider} status={r.status} "
                f"access={bool(r.access_token_encrypted)} refresh={bool(r.refresh_token_encrypted)}"
            )
            if r.status != IntegrationStatus.connected.value and has_tokens:
                r.status = IntegrationStatus.connected.value
                healed += 1
                print(f"    -> healed to connected")
        if healed:
            db.commit()
        print(f"healed={healed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
