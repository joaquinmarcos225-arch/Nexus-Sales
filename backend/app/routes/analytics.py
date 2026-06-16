"""Agregados para dashboard (sin datos monetarios)."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_company
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import PipelineStage, ProspectStatus
from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsDashboardRead,
    AnalyticsTotals,
    CampaignAnalyticsRow,
    CommercialSnapshot,
    CompanyAnalyticsRead,
    IntelligenceSnapshot,
    RecommendedActionItem,
    ResponsesByCampaignPoint,
    SellerAnalyticsRow,
    WeeklyMeetingsPoint,
)
from app.services.followup_engine import count_inbound_prospect_messages
from app.services import outreach_metrics as om
from app.services.recommended_actions import load_curated_tasks

router = APIRouter(prefix="/companies", tags=["analytics"])

TASK_KIND_LABELS: dict[str, str] = {
    "scheduled_followup": "Seguimiento programado",
    "review_inbound": "Revisar respuesta del prospecto",
    "awaiting_reply": "Esperar réplica del prospecto",
    "hot_lead": "Lead con alto interés — actuar",
}

STATUSES_ACTIVE = frozenset(
    {
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.contacted.value,
        ProspectStatus.replied.value,
        ProspectStatus.interested.value,
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
    }
)

STATUSES_CONTACTED = frozenset(
    {
        ProspectStatus.contacted.value,
        ProspectStatus.replied.value,
        ProspectStatus.interested.value,
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
    }
)

STATUSES_RESPONDED = frozenset(
    {
        ProspectStatus.replied.value,
        ProspectStatus.interested.value,
        ProspectStatus.not_interested.value,
    }
)


def _rate(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return round(min(1.0, n / d), 4)


def build_company_analytics_read(db: Session, company_id: int) -> CompanyAnalyticsRead:
    camps = db.scalars(select(Campaign).where(Campaign.company_id == company_id)).all()

    use_real = om.is_real_mode()

    campaigns_active = sum(1 for c in camps if c.status in ("running", "ready"))
    campaigns_paused = sum(1 for c in camps if c.status == "paused")
    campaigns_other = len(camps) - campaigns_active - campaigns_paused

    status_rows = db.execute(
        select(Prospect.status, func.count(Prospect.id))
        .where(Prospect.company_id == company_id)
        .where(om.exclude_testing_commercial_prospects())
        .group_by(Prospect.status)
    ).all()
    smap: dict[str, int] = {str(r[0]): int(r[1]) for r in status_rows}

    def sm(key: str) -> int:
        return smap.get(key, 0)

    prospects_imported = sum(sm(s.value) for s in ProspectStatus)
    prospects_active = sum(sm(s) for s in STATUSES_ACTIVE)
    if use_real:
        prospects_contacted_group = om.distinct_prospects_with_real_gmail_outbound_company(db, company_id)
        prospects_responded = om.distinct_prospects_with_real_gmail_inbound_company(db, company_id)
    else:
        prospects_contacted_group = om.distinct_prospects_with_outbound_company(db, company_id)
        prospects_responded = om.distinct_prospects_with_inbound_company(db, company_id)
    prospects_interested = sm(ProspectStatus.interested.value)
    meetings_booked = sm(ProspectStatus.meeting_booked.value)

    if use_real:
        messages_sent = int(
            db.scalar(
                select(func.count(OutreachMessage.id))
                .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
                .where(
                    Prospect.company_id == company_id,
                    OutreachMessage.direction == "outbound",
                    OutreachMessage.sender_type == "user",
                    OutreachMessage.gmail_message_id.isnot(None),
                )
            )
            or 0
        )
    else:
        messages_sent = int(
            db.scalar(
                select(func.count(OutreachMessage.id))
                .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
                .where(
                    Prospect.company_id == company_id,
                    OutreachMessage.direction == "outbound",
                )
            )
            or 0
        )

    lm = db.scalar(
        select(func.max(OutreachMessage.created_at)).join(
            Prospect, OutreachMessage.prospect_id == Prospect.id
        ).where(Prospect.company_id == company_id)
    )
    lp = db.scalar(select(func.max(Prospect.updated_at)).where(Prospect.company_id == company_id))
    last_candidates = [x for x in (lm, lp) if x is not None]
    last_activity_at = max(last_candidates) if last_candidates else None

    response_rate = _rate(prospects_responded, prospects_contacted_group)
    interest_rate = _rate(prospects_interested, prospects_responded)

    # Por campaña + status
    pcs = db.execute(
        select(Prospect.campaign_id, Prospect.status, func.count(Prospect.id))
        .where(Prospect.company_id == company_id)
        .where(om.exclude_testing_commercial_prospects())
        .group_by(Prospect.campaign_id, Prospect.status)
    ).all()

    pivot: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for cid, st, cnt in pcs:
        pivot[int(cid)][str(st)] = int(cnt)

    all_meetings: list[Meeting] = list(
        db.scalars(select(Meeting).where(Meeting.company_id == company_id)).all()
    )
    meetings_by_camp: dict[int, int] = defaultdict(int)
    for m in all_meetings:
        meetings_by_camp[m.campaign_id] += 1

    users = {u.id: u for u in db.scalars(select(User).where(User.company_id == company_id)).all()}

    campaign_rows: list[CampaignAnalyticsRow] = []
    responses_by_campaign: list[ResponsesByCampaignPoint] = []

    for c in sorted(camps, key=lambda x: x.name.lower()):
        pv = pivot.get(c.id, {})
        act = sum(pv.get(s, 0) for s in STATUSES_ACTIVE)
        if use_real:
            contact = om.distinct_prospects_with_real_gmail_outbound_campaign(db, c.id)
            resp = om.distinct_prospects_with_real_gmail_inbound_campaign(db, c.id)
        else:
            contact = om.distinct_prospects_with_outbound_campaign(db, c.id)
            resp = om.distinct_prospects_with_inbound_campaign(db, c.id)
        intr = pv.get(ProspectStatus.interested.value, 0)
        meet = pv.get(ProspectStatus.meeting_booked.value, 0)
        not_intr = pv.get(ProspectStatus.not_interested.value, 0)
        replied_only = pv.get(ProspectStatus.replied.value, 0)

        last_camp = db.scalar(
            select(func.max(OutreachMessage.created_at)).where(OutreachMessage.campaign_id == c.id)
        )

        msg_q = (
            select(func.count(OutreachMessage.id))
            .where(
                OutreachMessage.campaign_id == c.id,
                OutreachMessage.direction == "outbound",
            )
        )
        if use_real:
            msg_q = msg_q.where(
                OutreachMessage.sender_type == "user",
                OutreachMessage.gmail_message_id.isnot(None),
            )
        msg_c = int(db.scalar(msg_q) or 0)

        seller = users.get(c.seller_id)
        campaign_rows.append(
            CampaignAnalyticsRow(
                campaign_id=c.id,
                name=c.name,
                status=c.status,
                seller_id=c.seller_id,
                seller_name=seller.name if seller else "—",
                prospects_active=act,
                prospects_contacted=contact,
                prospects_responded=resp,
                prospects_interested=intr,
                prospects_not_interested=not_intr,
                prospects_replied=replied_only,
                meetings=meet,
                meetings_scheduled=meetings_by_camp.get(c.id, 0),
                messages_sent=msg_c,
                last_activity_at=last_camp,
            )
        )
        responses_by_campaign.append(
            ResponsesByCampaignPoint(campaign_id=c.id, campaign_name=c.name, responses=resp)
        )

    responses_by_campaign.sort(key=lambda x: x.responses, reverse=True)

    # Vendedores / SDR
    sellers_list = [u for u in users.values() if u.role == "seller"]
    seller_rows: list[SellerAnalyticsRow] = []
    camp_ids_by_seller: dict[int, list[int]] = defaultdict(list)
    for c in camps:
        camp_ids_by_seller[c.seller_id].append(c.id)

    for u in sorted(sellers_list, key=lambda x: x.name.lower()):
        ids = camp_ids_by_seller.get(u.id, [])
        if not ids:
            seller_rows.append(
                SellerAnalyticsRow(
                    user_id=u.id,
                    name=u.name,
                    email=u.email,
                    prospects_in_campaigns=0,
                    prospects_active=0,
                    messages_sent=0,
                    responses=0,
                    interested=0,
                    meetings=0,
                    pending_tasks=0,
                    active_campaigns=0,
                    response_rate=0.0,
                    interest_rate=0.0,
                    last_activity_at=None,
                )
            )
            continue

        pr_sub = db.execute(
            select(Prospect.status, func.count(Prospect.id))
            .where(Prospect.company_id == company_id, Prospect.campaign_id.in_(ids))
            .group_by(Prospect.status)
        ).all()
        ps = {str(r[0]): int(r[1]) for r in pr_sub}
        p_total = sum(ps.values())
        p_active = sum(ps.get(s, 0) for s in STATUSES_ACTIVE)

        resp_s = (
            om.distinct_prospects_with_real_gmail_inbound_seller_campaigns(
                db, company_id=company_id, campaign_ids=ids
            )
            if use_real
            else om.distinct_prospects_with_inbound_seller_campaigns(
                db, company_id=company_id, campaign_ids=ids
            )
        )

        if use_real:
            msg_s = int(
                db.scalar(
                    select(func.count(OutreachMessage.id))
                    .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
                    .where(
                        Prospect.company_id == company_id,
                        Prospect.campaign_id.in_(ids),
                        OutreachMessage.direction == "outbound",
                        OutreachMessage.sender_type == "user",
                        OutreachMessage.gmail_message_id.isnot(None),
                    )
                )
                or 0
            )
        else:
            msg_s = int(
                db.scalar(
                    select(func.count(OutreachMessage.id))
                    .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
                    .where(
                        Prospect.company_id == company_id,
                        Prospect.campaign_id.in_(ids),
                        OutreachMessage.direction == "outbound",
                    )
                )
                or 0
            )

        intr_s = ps.get(ProspectStatus.interested.value, 0)
        meet_s = ps.get(ProspectStatus.meeting_booked.value, 0)

        pending = (
            ps.get(ProspectStatus.imported.value, 0)
            + ps.get(ProspectStatus.compatible.value, 0)
            + ps.get(ProspectStatus.contacted.value, 0)
        )

        contacted_s = (
            om.distinct_prospects_with_real_gmail_outbound_seller_campaigns(
                db, company_id=company_id, campaign_ids=ids
            )
            if use_real
            else om.distinct_prospects_with_outbound_seller_campaigns(
                db, company_id=company_id, campaign_ids=ids
            )
        )
        active_camps = sum(
            1 for c in camps if c.seller_id == u.id and c.status in ("running", "ready")
        )
        seller_rr = _rate(resp_s, contacted_s)
        seller_ir = _rate(intr_s, resp_s) if resp_s else 0.0
        last_seller_act = db.scalar(
            select(func.max(OutreachMessage.created_at))
            .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
            .where(Prospect.company_id == company_id, Prospect.campaign_id.in_(ids))
        )

        seller_rows.append(
            SellerAnalyticsRow(
                user_id=u.id,
                name=u.name,
                email=u.email,
                prospects_in_campaigns=p_total,
                prospects_active=p_active,
                messages_sent=msg_s,
                responses=resp_s,
                interested=intr_s,
                meetings=meet_s,
                pending_tasks=pending,
                active_campaigns=active_camps,
                response_rate=seller_rr,
                interest_rate=seller_ir,
                last_activity_at=last_seller_act,
            )
        )

    # Reuniones por semana (ISO), últimas 8 semanas con datos
    week_counts: dict[str, int] = defaultdict(int)
    for m in all_meetings:
        if m.meeting_status != "completed":
            continue
        y, wk, _ = m.scheduled_for.isocalendar()
        label = f"{y}-W{wk:02d}"
        week_counts[label] += 1

    weekly_meetings = [
        WeeklyMeetingsPoint(week_label=k, count=v)
        for k, v in sorted(week_counts.items(), key=lambda x: x[0])[-12:]
    ]

    totals = AnalyticsTotals(
        campaigns_active=campaigns_active,
        campaigns_paused=campaigns_paused,
        campaigns_other=max(0, campaigns_other),
        prospects_imported=prospects_imported,
        prospects_active=prospects_active,
        prospects_contacted=prospects_contacted_group,
        prospects_responded=prospects_responded,
        prospects_interested=prospects_interested,
        meetings_booked=meetings_booked,
        messages_sent=messages_sent,
        response_rate=response_rate,
        interest_rate=interest_rate,
        last_activity_at=last_activity_at,
    )

    hot_prospects = int(
        db.scalar(
            select(func.count(Prospect.id)).where(
                Prospect.company_id == company_id,
                or_(
                    Prospect.interest_level == "high",
                    Prospect.status == ProspectStatus.interested.value,
                ),
            )
        )
        or 0
    )

    obj_rows = db.execute(
        select(Prospect.objection_type, func.count(Prospect.id))
        .where(Prospect.company_id == company_id, Prospect.objection_type.isnot(None))
        .group_by(Prospect.objection_type)
    ).all()
    objections_top = [
        {"objection_type": str(r[0]), "count": int(r[1])}
        for r in sorted(obj_rows, key=lambda x: -int(x[1]))[:10]
    ]

    pending_scheduled_followups = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(
                OutreachTask.company_id == company_id,
                OutreachTask.status == "pending",
                OutreachTask.task_kind == "scheduled_followup",
            )
        )
        or 0
    )
    pending_tasks_total = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(
                OutreachTask.company_id == company_id,
                OutreachTask.status == "pending",
            )
        )
        or 0
    )
    ia_meeting_nudges = int(
        db.scalar(
            select(func.count(Prospect.id)).where(
                Prospect.company_id == company_id,
                Prospect.meeting_nudge_sent_at.isnot(None),
            )
        )
        or 0
    )

    interest_by_campaign: list[dict[str, int | str | float]] = []
    for c in camps:
        subs = db.scalars(select(Prospect).where(Prospect.campaign_id == c.id)).all()
        n = len(subs)
        if n == 0:
            continue
        hi = sum(1 for p in subs if (p.interest_level or "").lower() == "high")
        med = sum(1 for p in subs if (p.interest_level or "").lower() == "medium")
        lo = n - hi - med
        interest_by_campaign.append(
            {
                "campaign_id": c.id,
                "campaign_name": c.name,
                "high_pct": round(100 * hi / n, 1),
                "medium_pct": round(100 * med / n, 1),
                "low_pct": round(100 * max(lo, 0) / n, 1),
            }
        )

    industry_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"c": 0, "r": 0})
    all_prospects = db.scalars(select(Prospect).where(Prospect.company_id == company_id)).all()
    for p in all_prospects:
        ind = (p.industry or "—").strip() or "—"
        if p.status in STATUSES_CONTACTED:
            industry_buckets[ind]["c"] += 1
        if p.status in STATUSES_RESPONDED:
            industry_buckets[ind]["r"] += 1
    industry_response_rates: list[dict[str, int | str | float]] = []
    for ind, v in sorted(industry_buckets.items(), key=lambda x: -x[1]["c"])[:18]:
        ctc, rsp = v["c"], v["r"]
        industry_response_rates.append(
            {
                "industry": ind,
                "contacted": ctc,
                "responded": rsp,
                "rate": _rate(rsp, ctc),
            }
        )

    suggested_meeting_momentum = 0
    for p in all_prospects:
        if (p.interest_level or "").lower() != "high":
            continue
        if count_inbound_prospect_messages(db, p.id) >= 2:
            suggested_meeting_momentum += 1

    stg_rows = db.execute(
        select(Prospect.pipeline_stage, func.count(Prospect.id))
        .where(Prospect.company_id == company_id)
        .group_by(Prospect.pipeline_stage)
    ).all()
    pipeline_by_stage: dict[str, int] = {str(r[0]): int(r[1]) for r in stg_rows}

    closed_stages = {PipelineStage.cerrado_ganado.value, PipelineStage.cerrado_perdido.value}
    pipeline_open_count = int(
        db.scalar(
            select(func.count(Prospect.id)).where(
                Prospect.company_id == company_id,
                or_(
                    Prospect.pipeline_stage.is_(None),
                    Prospect.pipeline_stage.not_in(list(closed_stages)),
                ),
            )
        )
        or 0
    )

    m_pending = sum(1 for m in all_meetings if m.meeting_status == "pending")
    m_conf = sum(1 for m in all_meetings if m.meeting_status == "confirmed")
    m_done = sum(1 for m in all_meetings if m.meeting_status == "completed")
    m_can = sum(1 for m in all_meetings if m.meeting_status == "canceled")
    m_ns = sum(1 for m in all_meetings if m.meeting_status == "no_show")
    m_total = len(all_meetings)
    m_denom = sum(
        1 for m in all_meetings if m.meeting_status in ("pending", "confirmed", "completed")
    )
    m_rate = _rate(m_done, m_denom)

    camp_by_id = {c.id: c for c in camps}
    meet_cnt: dict[int, int] = defaultdict(int)
    for m in all_meetings:
        meet_cnt[m.campaign_id] += 1
    top_campaigns_by_meetings: list[dict[str, int | str | float]] = []
    for cid, n in sorted(meet_cnt.items(), key=lambda x: -x[1])[:10]:
        cc = camp_by_id.get(cid)
        top_campaigns_by_meetings.append(
            {"campaign_id": cid, "campaign_name": cc.name if cc else "—", "meetings": n}
        )

    commercial = CommercialSnapshot(
        meetings_pending=m_pending,
        meetings_confirmed=m_conf,
        meetings_completed=m_done,
        meetings_canceled=m_can,
        meetings_no_show=m_ns,
        meetings_total=m_total,
        meeting_completion_rate=m_rate,
        pipeline_by_stage=pipeline_by_stage,
        pipeline_open_count=pipeline_open_count,
        top_campaigns_by_meetings=top_campaigns_by_meetings,
    )

    intelligence = IntelligenceSnapshot(
        hot_prospects=hot_prospects,
        pending_scheduled_followups=pending_scheduled_followups,
        pending_tasks_total=pending_tasks_total,
        ia_meeting_nudges=ia_meeting_nudges,
        objections_top=objections_top,
        interest_by_campaign=interest_by_campaign,
        industry_response_rates=industry_response_rates,
        suggested_meeting_momentum=suggested_meeting_momentum,
    )

    return CompanyAnalyticsRead(
        totals=totals,
        prospect_status_breakdown=smap,
        intelligence=intelligence,
        commercial=commercial,
        campaigns=campaign_rows,
        sellers=seller_rows,
        weekly_meetings=weekly_meetings,
        responses_by_campaign=responses_by_campaign,
    )


@router.get("/{company_id}/analytics", response_model=CompanyAnalyticsRead)
def company_analytics(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> CompanyAnalyticsRead:
    return build_company_analytics_read(db, company_id)


def _recommended_action_item_from_curated(
    t: OutreachTask,
    headline: str,
    reason: str,
    suggested: str,
    action_label: str,
    score: int,
) -> RecommendedActionItem:
    camp = t.campaign
    pr = t.prospect
    return RecommendedActionItem(
        id=t.id,
        task_kind=t.task_kind,
        title=t.title,
        due_at=t.due_at,
        campaign_id=t.campaign_id,
        prospect_id=t.prospect_id,
        campaign_name=camp.name if camp else "—",
        prospect_name=(pr.name if pr else "") or "",
        prospect_company=(pr.company_name if pr else "") or "",
        action_label=action_label,
        headline=headline,
        reason=reason,
        suggested_action=suggested,
        priority_score=score,
    )


def build_analytics_dashboard_read(db: Session, company_id: int) -> AnalyticsDashboardRead:
    base = build_company_analytics_read(db, company_id)
    total_products = int(
        db.scalar(select(func.count(Product.id)).where(Product.company_id == company_id)) or 0
    )
    curated = load_curated_tasks(
        db,
        company_id=company_id,
        campaign_id=None,
        limit=5,
    )
    recommended = [
        _recommended_action_item_from_curated(t, h, r, s, lbl, sc)
        for t, h, r, s, lbl, sc in curated
    ]
    n_campaigns = len(base.campaigns)

    ch_rows = db.execute(
        select(OutreachMessage.channel, func.count(OutreachMessage.id))
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.direction == "outbound",
        )
        .group_by(OutreachMessage.channel)
    ).all()
    outreach_messages_by_channel = [
        {"channel": str(r[0]), "count": int(r[1])} for r in sorted(ch_rows, key=lambda x: -int(x[1]))
    ]

    prospects_no_reply = int(
        db.scalar(
            select(func.count(Prospect.id)).where(
                Prospect.company_id == company_id,
                Prospect.status == ProspectStatus.contacted.value,
            )
        )
        or 0
    )

    followups_sent_total = int(
        db.scalar(
            select(func.coalesce(func.sum(Prospect.followup_count), 0)).where(
                Prospect.company_id == company_id
            )
        )
        or 0
    )

    smap = base.prospect_status_breakdown

    def sm2(key: str) -> int:
        return int(smap.get(key, 0))

    responses_positive = sm2(ProspectStatus.interested.value) + sm2(ProspectStatus.meeting_booked.value)
    responses_negative = sm2(ProspectStatus.not_interested.value)
    responses_neutral = sm2(ProspectStatus.replied.value)
    responses_wants_meeting = sm2(ProspectStatus.meeting_booked.value)

    obj_all = db.execute(
        select(Prospect.objection_type, func.count(Prospect.id))
        .where(Prospect.company_id == company_id, Prospect.objection_type.isnot(None))
        .group_by(Prospect.objection_type)
    ).all()
    objection_counts: dict[str, int] = {str(r[0]): int(r[1]) for r in obj_all}

    obj_by_camp_rows = db.execute(
        select(Prospect.campaign_id, Prospect.objection_type, func.count(Prospect.id))
        .where(Prospect.company_id == company_id, Prospect.objection_type.isnot(None))
        .group_by(Prospect.campaign_id, Prospect.objection_type)
    ).all()
    obj_by_camp: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for cid, ot, cnt in obj_by_camp_rows:
        obj_by_camp[int(cid)].append((str(ot), int(cnt)))

    high_by_camp_rows = db.execute(
        select(Prospect.campaign_id, func.count(Prospect.id))
        .where(Prospect.company_id == company_id, Prospect.interest_level == "high")
        .group_by(Prospect.campaign_id)
    ).all()
    high_by_camp = {int(r[0]): int(r[1]) for r in high_by_camp_rows}

    responses_campaign_detail: list[dict] = []
    for row in base.campaigns:
        top_obj = "—"
        pairs = sorted(obj_by_camp.get(row.campaign_id, []), key=lambda x: -x[1])
        if pairs:
            top_obj = pairs[0][0]
        contacted = row.prospects_contacted or 0
        rate_c = _rate(row.prospects_responded, contacted) if contacted else 0.0
        responses_campaign_detail.append(
            {
                "campaign_name": row.name,
                "responses_total": row.prospects_responded,
                "positive": row.prospects_interested + row.meetings,
                "negative": row.prospects_not_interested,
                "neutral": row.prospects_replied,
                "high_interest": high_by_camp.get(row.campaign_id, 0),
                "top_objection": top_obj,
                "response_rate": rate_c,
            }
        )

    since = datetime.now(UTC) - timedelta(days=120)
    inbound_msgs = db.scalars(
        select(OutreachMessage)
        .join(Prospect, OutreachMessage.prospect_id == Prospect.id)
        .where(
            Prospect.company_id == company_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.created_at >= since,
            om.exclude_testing_messages(),
        )
        .order_by(OutreachMessage.created_at.asc())
    ).all()
    week_in_counts: dict[str, int] = defaultdict(int)
    for m in inbound_msgs:
        y, wk, _ = m.created_at.isocalendar()
        week_in_counts[f"{y}-W{wk:02d}"] += 1
    weekly_inbound_responses = [
        WeeklyMeetingsPoint(week_label=k, count=v)
        for k, v in sorted(week_in_counts.items(), key=lambda x: x[0])[-16:]
    ]

    scatter_response_vs_messages: list[dict] = []
    for row in base.campaigns:
        msg_n = row.messages_sent or 0
        resp_n = row.prospects_responded or 0
        scatter_response_vs_messages.append(
            {
                "campaign": row.name,
                "messages_sent": msg_n,
                "responses": resp_n,
                "response_rate": _rate(resp_n, msg_n) if msg_n else 0.0,
            }
        )

    hist_bins = [0, 20, 40, 60, 80, 100]
    hist_counts = [0] * (len(hist_bins) - 1)
    probs = db.scalars(select(Prospect.interest_probability).where(Prospect.company_id == company_id)).all()
    for pr in probs:
        v = int(pr or 0)
        v = max(0, min(100, v))
        for i in range(len(hist_bins) - 1):
            lo, hi = hist_bins[i], hist_bins[i + 1]
            if i == len(hist_bins) - 2:
                if lo <= v <= hi:
                    hist_counts[i] += 1
                    break
            elif lo <= v < hi:
                hist_counts[i] += 1
                break
    interest_histogram = [
        {"bucket": f"{hist_bins[i]}-{hist_bins[i + 1]}", "count": hist_counts[i]}
        for i in range(len(hist_counts))
    ]

    reply_deltas: list[float] = []
    for p in db.scalars(
        select(Prospect)
        .where(
            Prospect.company_id == company_id,
            Prospect.status.in_(tuple(STATUSES_RESPONDED)),
        )
        .limit(5000)
    ).all():
        if p.last_inbound_at and p.last_outbound_at:
            try:
                delta_h = (p.last_inbound_at - p.last_outbound_at).total_seconds() / 3600.0
                if delta_h >= 0:
                    reply_deltas.append(delta_h)
            except Exception:
                continue
    avg_reply_hours = round(sum(reply_deltas) / len(reply_deltas), 2) if reply_deltas else None

    return AnalyticsDashboardRead(
        totals=base.totals,
        prospect_status_breakdown=base.prospect_status_breakdown,
        intelligence=base.intelligence,
        commercial=base.commercial,
        campaigns=base.campaigns,
        sellers=base.sellers,
        weekly_meetings=base.weekly_meetings,
        responses_by_campaign=base.responses_by_campaign,
        total_campaigns=n_campaigns,
        active_campaigns=base.totals.campaigns_active,
        total_products=total_products,
        total_prospects=base.totals.prospects_imported,
        contacted_prospects=base.totals.prospects_contacted,
        replied_prospects=base.totals.prospects_responded,
        interested_prospects=base.totals.prospects_interested,
        booked_meetings=base.totals.meetings_booked,
        pending_followups=base.intelligence.pending_scheduled_followups,
        hot_prospects=base.intelligence.hot_prospects,
        meetings_pending=base.commercial.meetings_pending,
        meetings_completed=base.commercial.meetings_completed,
        campaigns_summary=list(base.campaigns),
        team_summary=list(base.sellers),
        funnel=dict(base.prospect_status_breakdown),
        recommended_actions=recommended,
        outreach_messages_by_channel=outreach_messages_by_channel,
        prospects_no_reply=prospects_no_reply,
        followups_sent_total=int(followups_sent_total),
        responses_positive=responses_positive,
        responses_negative=responses_negative,
        responses_neutral=responses_neutral,
        responses_wants_meeting=responses_wants_meeting,
        objection_counts=objection_counts,
        responses_campaign_detail=responses_campaign_detail,
        weekly_inbound_responses=weekly_inbound_responses,
        scatter_response_vs_messages=scatter_response_vs_messages,
        interest_histogram=interest_histogram,
        avg_reply_hours=avg_reply_hours,
    )


analytics_dashboard_router = APIRouter(tags=["analytics"])


@analytics_dashboard_router.get("/analytics", response_model=AnalyticsDashboardRead)
def analytics_dashboard(
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    db: Session = Depends(get_db),
) -> AnalyticsDashboardRead:
    import logging
    import time

    from sqlalchemy.exc import OperationalError

    log = logging.getLogger("nexus.http")
    t0 = time.perf_counter()
    log.info("[analytics] dashboard company_id=%s start", company_id)
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    try:
        result = build_analytics_dashboard_read(db, company_id)
    except OperationalError as exc:
        log.exception(
            "[analytics] dashboard sqlite_busy company_id=%s elapsed_ms=%s",
            company_id,
            int((time.perf_counter() - t0) * 1000),
        )
        raise HTTPException(status_code=503, detail="Base de datos ocupada. Reintentá.") from exc
    except Exception as exc:
        log.exception(
            "[analytics] dashboard failed company_id=%s elapsed_ms=%s",
            company_id,
            int((time.perf_counter() - t0) * 1000),
        )
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo calcular analítica: {type(exc).__name__}",
        ) from exc
    log.info(
        "[analytics] dashboard company_id=%s done elapsed_ms=%s campaigns=%s",
        company_id,
        int((time.perf_counter() - t0) * 1000),
        len(result.campaigns),
    )
    return result
