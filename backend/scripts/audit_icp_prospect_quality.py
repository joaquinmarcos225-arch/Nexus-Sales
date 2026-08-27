"""Audit prospect quality vs campaign ICP."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services.lead_sourcing.icp_score_audit import compute_icp_score_breakdown


def camp_dict(c: Campaign) -> dict[str, str]:
    return {
        "target_industry": c.target_industry or "",
        "target_role": c.target_role or "",
        "target_country": c.target_country or "",
        "target_company_size": c.target_company_size or "",
        "target_area": getattr(c, "target_area", None) or "",
        "target_language": c.target_language or "",
    }


def role_hit(target: str, role: str) -> bool:
    tr = (target or "").lower().strip()
    r = (role or "").lower().strip()
    if not tr or not r:
        return False
    if tr in r or r in tr:
        return True
    tokens = [t.strip() for t in tr.replace("/", ",").split(",") if t.strip()]
    if any(t in r for t in tokens):
        return True
    if "sales" in tr:
        sales = ("sales", "comercial", "revenue", "cro", "vendedor", "account executive", "ae ")
        return any(t in r for t in sales)
    return False


def industry_hit(target: str, industry: str, company: str) -> bool:
    ti = (target or "").lower().strip()
    ind = (industry or "").lower().strip()
    co = (company or "").lower().strip()
    if not ti:
        return False
    blob = f"{ind} {co}"
    if ti in blob or ind in ti:
        return True
    if "saas" in ti:
        return any(x in blob for x in ("saas", "software", "tecnolog", "tech", "cloud", "platform"))
    return False


def country_hit(target: str, country: str) -> bool:
    tc = (target or "").lower().strip()
    c = (country or "").lower().strip()
    if not tc or not c:
        return False
    if any(x in c for x in ("brasil", "brazil", "são paulo", "sao paulo", "rio de janeiro")):
        if "brasil" in tc or "brazil" in tc or "latam" in tc:
            return True
    if "latam" in tc and any(
        x in c
        for x in (
            "argentina",
            "mexico",
            "méxico",
            "chile",
            "colombia",
            "peru",
            "latam",
            "brasil",
            "brazil",
        )
    ):
        return True
    return tc in c or c in tc


def rich_score(breakdown) -> int:
    if breakdown is None:
        return -1
    if isinstance(breakdown, dict):
        return int(breakdown.get("final_score") or breakdown.get("score") or 0)
    return int(getattr(breakdown, "final_score", None) or getattr(breakdown, "score", 0) or 0)


def audit_campaign(db, cid: int) -> dict | None:
    c = db.get(Campaign, cid)
    if c is None:
        return None
    icp = camp_dict(c)
    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == cid).order_by(Prospect.id)).all()
    people = []
    for p in rows:
        br = compute_icp_score_breakdown(
            campaign_industry=icp["target_industry"] or None,
            campaign_country=icp["target_country"] or None,
            campaign_role=icp["target_role"] or None,
            campaign_company_size=icp["target_company_size"] or None,
            prospect_industry=p.industry,
            prospect_country=p.country,
            prospect_role=p.role,
            email=p.email,
            linkedin_url=p.linkedin_url,
            legacy_compatibility_score=p.compatibility_score,
        )
        rich = rich_score(br)
        role_req = bool(icp["target_role"].strip())
        people.append(
            {
                "id": p.id,
                "name": p.name,
                "role": p.role,
                "company": p.company_name,
                "industry": p.industry,
                "country": p.country,
                "status": p.status,
                "db_score": p.compatibility_score,
                "rich_score": rich,
                "role_ok": role_hit(icp["target_role"], p.role or "") if role_req else None,
                "industry_ok": industry_hit(
                    icp["target_industry"], p.industry or "", p.company_name or ""
                ),
                "country_ok": country_hit(icp["target_country"], p.country or ""),
                "reason": (p.score_reason or "")[:180],
            }
        )

    n = len(people) or 1
    role_req = bool(icp["target_role"].strip())
    return {
        "campaign_id": c.id,
        "name": c.name,
        "status": c.status,
        "icp": {
            "industry": c.target_industry,
            "role": c.target_role,
            "country": c.target_country,
            "size": c.target_company_size,
            "area": c.target_area,
        },
        "n": len(people),
        "avg_db": round(sum((p["db_score"] or 0) for p in people) / n, 1),
        "avg_rich": round(sum(p["rich_score"] for p in people) / n, 1),
        "below_70": sum(1 for p in people if p["rich_score"] < 70),
        "role_fit_pct": round(100 * sum(1 for p in people if p["role_ok"]) / n) if role_req else None,
        "industry_fit_pct": round(100 * sum(1 for p in people if p["industry_ok"]) / n),
        "country_fit_pct": round(100 * sum(1 for p in people if p["country_ok"]) / n),
        "people": people,
    }


def main() -> None:
    as_json = "--json" in sys.argv
    db = SessionLocal()
    try:
        reports = []
        for cid in (3, 4):
            rep = audit_campaign(db, cid)
            if rep:
                reports.append(rep)
                if not as_json:
                    print("=" * 72)
                    print(f"CAMPAIGN {rep['campaign_id']}: {rep['name']}")
                    print(f"ICP: {rep['icp']}")
                    print(
                        f"n={rep['n']} avg_db={rep['avg_db']} avg_rich={rep['avg_rich']} "
                        f"below70={rep['below_70']} role%={rep['role_fit_pct']} "
                        f"ind%={rep['industry_fit_pct']} ctry%={rep['country_fit_pct']}"
                    )
                    for p in rep["people"]:
                        flag = "OK" if p["rich_score"] >= 70 else ("MID" if p["rich_score"] >= 50 else "LOW")
                        print(
                            f"  [{flag}] {p['id']} rich={p['rich_score']} db={p['db_score']} "
                            f"{p['name']} | {p['role']} @ {p['company']}"
                        )
        if as_json:
            out = Path(__file__).resolve().parent / "audit_icp_prospect_quality.json"
            out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
            print(str(out))
    finally:
        db.close()


if __name__ == "__main__":
    main()
