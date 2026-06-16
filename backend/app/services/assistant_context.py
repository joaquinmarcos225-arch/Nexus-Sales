"""Resumen textual de datos Nexus para el asistente interno."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.prospect import Prospect
from app.schemas.campaign_channels import coerce_allowed_channels


def build_company_snapshot(db: Session, company_id: int, *, limit_campaigns: int = 35) -> str:
    camps = db.scalars(
        select(Campaign)
        .where(Campaign.company_id == company_id)
        .order_by(Campaign.created_at.desc())
        .limit(limit_campaigns)
    ).all()

    lines = [f"Empresa id={company_id}. Campañas recientes ({len(camps)} cargadas):"]
    campaign_ids = [c.id for c in camps]

    totals = dict(
        db.execute(
            select(Prospect.status, func.count(Prospect.id)).where(
                Prospect.company_id == company_id
            ).group_by(Prospect.status)
        ).all()
    )
    pt = ProspectStatus

    friendly = [
        ("imported", totals.get(pt.imported.value, 0)),
        ("compatible", totals.get(pt.compatible.value, 0)),
        ("not_compatible", totals.get(pt.not_compatible.value, 0)),
        ("contacted", totals.get(pt.contacted.value, 0)),
        ("replied", totals.get(pt.replied.value, 0)),
        ("interested", totals.get(pt.interested.value, 0)),
        ("not_interested", totals.get(pt.not_interested.value, 0)),
        ("meeting_booked", totals.get(pt.meeting_booked.value, 0)),
        ("failed", totals.get(pt.failed.value, 0)),
    ]
    lines.append("Distribución prospects por estado:")
    lines.extend([f"- {lab}: {n}" for lab, n in friendly])

    replied_like = totals.get(pt.replied.value, 0) + totals.get(pt.interested.value, 0)
    contacted_any = totals.get(pt.contacted.value, 0) + replied_like
    denom = replied_like + totals.get(pt.not_interested.value, 0) + totals.get(pt.failed.value, 0)
    if denom > 0:
        approx = replied_like / denom
        lines.append(
            f"Aprox proporción resonancia positiva (replied+interested vs replies negativos+failed): "
            f"{approx:.2f}"
        )

    respond_rate = replied_like / contacted_any if contacted_any else None
    if respond_rate is not None:
        lines.append(f"Tasa muy gruesa de respuesta plausible (sobre contacted+): {respond_rate:.2f}")

    if campaign_ids:
        agg = db.execute(
            select(Prospect.campaign_id, Prospect.status, func.count(Prospect.id))
            .where(Prospect.campaign_id.in_(campaign_ids))
            .group_by(Prospect.campaign_id, Prospect.status)
        ).all()
        by_c: dict[int, dict[str, int]] = {}
        for cid, status, cnt in agg:
            by_c.setdefault(int(cid), {})[status] = cnt
        lines.append("Detalle campaña (solo ids listados recientemente):")
        for c in camps[:12]:
            m = by_c.get(c.id, {})
            raw_ch = getattr(c, "allowed_channels", None)
            lc = ",".join(coerce_allowed_channels(raw_ch))
            lines.append(
                f"- id={c.id} nombre={c.name!r} status={c.status} "
                f"canales_habilitados={lc} prospects={sum(m.values())} "
                f"interested={m.get(pt.interested.value,0)} "
                f"replied={m.get(pt.replied.value,0)} "
                f"failed={m.get(pt.failed.value,0)} "
                f"not_interested={m.get(pt.not_interested.value,0)}"
            )
        if len(camps) > 12:
            lines.append(f"... {len(camps) - 12} campañas más truncadas.")

    lines.append("(Snapshot auto-generado para IA; algunos denominadores pueden ser incompletos).")
    return "\n".join(lines)
