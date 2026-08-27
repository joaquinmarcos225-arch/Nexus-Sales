"""WhatsApp prioritario en sourcing: enrich-person por móvil completo."""

from types import SimpleNamespace
from unittest.mock import patch

from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.campaign_sequence_channels import campaign_requires_whatsapp
from app.services.lead_sourcing.mapper import _pick_phone, to_prospect_create
from app.services.lead_sourcing.prospeo_phone import person_has_usable_mobile
from app.services.lead_sourcing.role_person_search import _maybe_enrich_if_needed
from app.services.whatsapp_cloud_service import (
    is_usable_phone,
    sanitize_stored_email,
    sanitize_stored_phone,
)


def test_campaign_requires_whatsapp_from_allowed_channels():
    c = SimpleNamespace(
        allowed_channels='["linkedin","email","whatsapp"]',
        sequence_plan=None,
    )
    assert campaign_requires_whatsapp(c) is True


def test_campaign_requires_whatsapp_from_sequence_plan():
    c = SimpleNamespace(
        allowed_channels='["linkedin","email"]',
        sequence_plan={
            "mode": "fixed",
            "touches": [{"day": 4, "channel": "whatsapp"}],
        },
    )
    assert campaign_requires_whatsapp(c) is True


def test_masked_email_not_usable():
    assert sanitize_stored_email("m*******@corp.com") is None
    assert sanitize_stored_email("real@corp.com") == "real@corp.com"


def test_person_has_usable_mobile_rejects_masked_preview():
    person = {"mobile": {"mobile": "+54 9 342 6**-****", "revealed": False}}
    assert person_has_usable_mobile(person) is False


def test_person_has_usable_mobile_accepts_full_number():
    person = {"mobile": {"mobile": "+5493426123456", "revealed": True}}
    assert person_has_usable_mobile(person) is True


def test_person_has_usable_mobile_rejects_landline_direct_phone():
    person = {
        "phone": "+54 11 4376-2000",
        "direct_phone": "+54 11 4376-2000",
    }
    assert person_has_usable_mobile(person) is False


def test_extract_prospeo_phones_only_from_mobile_object():
    from app.services.lead_sourcing.prospeo_phone import extract_prospeo_phones

    landline_only = extract_prospeo_phones({"phone": "+54 11 4376-2000"})
    assert landline_only[0] is None
    assert landline_only[1] is not None
    assert landline_only[2] is None
    mobile = extract_prospeo_phones(
        {"mobile": {"mobile": "+5491128942875", "revealed": True, "status": "VERIFIED"}}
    )
    assert mobile[0] == "+5491128942875"
    assert mobile[2] == "+5491128942875"


def test_sanitize_rejects_short_and_masked():
    assert sanitize_stored_phone("+54 9 342 6**-****") is None
    assert sanitize_stored_phone("123") is None
    assert is_usable_phone("+5491122334455") is True


def test_pick_phone_ignores_masked():
    phone, wa = _pick_phone(
        {
            "phone_numbers": [
                {"type": "mobile", "raw_number": "+54 9 11 ****-****"},
                {"type": "mobile", "raw_number": "+5491122334455"},
            ]
        }
    )
    assert phone == "+5491122334455"
    assert wa == "+5491122334455"


def test_to_prospect_create_rejects_landline_as_whatsapp():
    cand = LeadCandidateRead(
        external_id="2",
        provider="prospeo",
        name="Bob",
        company_name="Acme",
        phone="+54 11 4376-2000",
        whatsapp="+54 11 4376-2000",
        email="bob@acme.com",
    )
    payload = to_prospect_create(cand)
    assert payload.whatsapp is None
    assert payload.landline_phone is not None


def test_to_prospect_create_strips_masked_phone():
    cand = LeadCandidateRead(
        external_id="1",
        provider="prospeo",
        name="Ana",
        company_name="Acme",
        phone="+54 9 342 6**-****",
        whatsapp="+54 9 342 6**-****",
        email="ana@acme.com",
    )
    payload = to_prospect_create(cand)
    assert payload.phone is None
    assert payload.whatsapp is None


def test_contact_details_filter_mobile_when_required():
    from app.services.lead_sourcing.prospeo_phone import contact_details_filter

    f = contact_details_filter(require_mobile=True, require_email=False)
    assert f["person_contact_details"]["mobile"] == ["VERIFIED"]


def test_person_mobile_verified():
    from app.services.lead_sourcing.prospeo_phone import person_mobile_verified

    assert person_mobile_verified({"mobile": {"status": "VERIFIED"}}) is True
    assert person_mobile_verified({"mobile": {"status": "UNAVAILABLE"}}) is False


def test_maybe_enrich_when_require_mobile_and_masked_preview():
    person = {
        "person_id": "abc123",
        "email": {"email": "real@corp.com"},
        "mobile": {"mobile": "+54 9 342 6**-****"},
        "company": {"name": "Acme"},
    }
    enriched = {
        "person_id": "abc123",
        "mobile": {"mobile": "+5493426123456", "revealed": True},
    }
    with patch(
        "app.services.lead_sourcing.role_person_search.enrich_person_by_id",
        return_value=enriched,
    ) as mock_enrich:
        out = _maybe_enrich_if_needed(person, require_mobile=True)
    mock_enrich.assert_called_once_with("abc123", require_mobile=True)
    assert person_has_usable_mobile(out) is True


def test_maybe_enrich_skips_when_mobile_ok_without_require():
    person = {
        "person_id": "abc123",
        "email": {"email": "real@corp.com"},
        "mobile": {"mobile": "+5493426123456", "revealed": True},
        "company": {"name": "Acme"},
    }
    with patch(
        "app.services.lead_sourcing.role_person_search.enrich_person_by_id",
    ) as mock_enrich:
        out = _maybe_enrich_if_needed(person, require_mobile=False)
    mock_enrich.assert_not_called()
    assert out is person
