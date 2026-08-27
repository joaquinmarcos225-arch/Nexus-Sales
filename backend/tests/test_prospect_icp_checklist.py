from types import SimpleNamespace

from app.services.prospect_icp_checklist import build_prospect_icp_checklist
from app.services.manual_prospect_channel_enrichment import format_channel_find_summary


def test_icp_checklist_disabled():
    assert build_prospect_icp_checklist(SimpleNamespace(), SimpleNamespace()) == []


def test_channel_summary_includes_linkedin_when_found():
    p = SimpleNamespace(
        email="a@b.com",
        phone="+549111",
        whatsapp=None,
        linkedin_url="https://www.linkedin.com/in/alberto-garcia",
        channel_enrich_status="done",
    )
    summary = format_channel_find_summary(
        needed={"email", "phone", "linkedin"},
        prospect=p,
        enrich_status="done",
    )
    assert "Gmail encontrado" in summary
    assert "LinkedIn encontrado" in summary
    assert "WhatsApp encontrado" in summary


def test_channel_summary_linkedin_missing():
    p = SimpleNamespace(
        email="a@b.com",
        phone=None,
        whatsapp=None,
        linkedin_url=None,
        channel_enrich_status="done",
    )
    summary = format_channel_find_summary(
        needed={"email", "linkedin", "phone"},
        prospect=p,
        enrich_status="done",
    )
    assert "LinkedIn no encontrado" in summary
    assert "WhatsApp no encontrado" in summary
