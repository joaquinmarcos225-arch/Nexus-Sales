"""Seed linkedin_profile_urn for Dominique (id=25) so Enviar mensaje opens compose direct."""
from __future__ import annotations

import sqlite3
from pathlib import Path

URN = "ACoAAA8Beb4BfGZq8ak6hBjfZQu-4SitcIJzt9o"
DB = Path(__file__).resolve().parents[1] / "data" / "nexus_sales.db"


def main() -> None:
    conn = sqlite3.connect(DB)
    cols = {r[1] for r in conn.execute("pragma table_info(prospects)")}
    if "linkedin_profile_urn" not in cols:
        conn.execute("ALTER TABLE prospects ADD COLUMN linkedin_profile_urn TEXT")
        print("added column linkedin_profile_urn")
    conn.execute(
        "UPDATE prospects SET linkedin_profile_urn = ? WHERE id = 25",
        (URN,),
    )
    row = conn.execute(
        "SELECT id, name, linkedin_profile_urn FROM prospects WHERE id = 25"
    ).fetchone()
    conn.commit()
    conn.close()
    print("updated", row)


if __name__ == "__main__":
    main()
