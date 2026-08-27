"""Genera y valida los 7 toques del playbook para un prospecto."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

from dotenv import load_dotenv

load_dotenv(_backend / ".env", override=True)

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.session import SessionLocal
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services.ai_instruction_context import campaign_education_blob
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK
from app.services.lead_sourcing.nexus_outreach_mvp import _product_dict
from app.services.lead_sourcing.sdr_playbook_outreach import (
    SdrDraftValidationError,
    _validate_body,
    generate_sdr_playbook_touch,
)
from app.services.openai_fallback import apply_fallback_marker_to_body, build_sdr_playbook_fallback_json
from app.services.sdr_outreach_compose import campaign_dict_for_sdr, prospect_dict_for_sdr

PROSPECT_ID = 10
CAMPAIGN_ID = 4


def main() -> None:
    db = SessionLocal()
    try:
        campaign = db.scalars(
            select(Campaign).where(Campaign.id == CAMPAIGN_ID).options(selectinload(Campaign.product))
        ).first()
        prospect = db.get(Prospect, PROSPECT_ID)
        if not campaign or not prospect:
            print("Campaña o prospecto no encontrado")
            return

        education = campaign_education_blob(db, campaign)
        camp = campaign_dict_for_sdr(db, campaign)
        prod = _product_dict(campaign)
        pros = prospect_dict_for_sdr(prospect)

        prior: list[dict] = []
        for step in DEFAULT_MVP_PLAYBOOK:
            try:
                subj, body, _ = generate_sdr_playbook_touch(
                    channel=step.channel,
                    prospect=pros,
                    campaign=camp,
                    product=prod,
                    education=education,
                    step_day=step.day,
                    step_objective=step.objective,
                    prior_touches=prior,
                    tone=campaign.tone or "",
                )
                source = "IA"
                extra_issues: list[str] = []
            except SdrDraftValidationError as exc:
                raw = build_sdr_playbook_fallback_json(
                    channel=step.channel,
                    prospect=pros,
                    campaign=camp,
                    product=prod,
                    step_day=step.day,
                    step_objective=step.objective,
                    prior_touches=prior,
                )
                data = json.loads(raw)
                body = apply_fallback_marker_to_body((data.get("body") or "").strip())
                body = body.replace("[FALLBACK TEST]", "").strip()
                subj = (data.get("subject") or "").strip() or None
                source = "fallback"
                extra_issues = list(exc.report.get("issues") or [])[:5]

            acc = _validate_body(
                step.channel,
                body,
                subject=subj,
                first_touch=step.day == 1,
                step_day=step.day,
                sender_name=camp["sender_name"],
                brand_name=camp["brand_name"],
                prospect=pros,
                campaign=camp,
                product=prod,
            )
            issues = acc.issues[:5] if acc.issues else extra_issues

            print("=" * 60)
            print(
                f"DÍA {step.day} | {step.channel.upper()} | {source} | "
                f"valid={'OK' if not issues else 'WARN'} | {len(body)} chars"
            )
            print(step.objective)
            if issues:
                print("ISSUES:", issues)
            if subj:
                print(f"Asunto: {subj}")
            print()
            print(body)
            print()

            touch: dict = {"day": step.day, "channel": step.channel, "body": body}
            if subj:
                touch["subject"] = subj
            prior.append(touch)
    finally:
        db.close()


if __name__ == "__main__":
    main()
