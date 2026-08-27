"""Enrich bajo demanda de canales para prospectos manuales."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.manual_prospect_channel_enrichment import (
    channels_needed_from_sequence_plan,
    enrich_missing_channels,
)


def _prospect(**kw):
    defaults = dict(
        id=1,
        name="Ana Pérez",
        company_name="Acme Latam",
        company_website=None,
        email=None,
        linkedin_url=None,
        phone=None,
        whatsapp=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_channels_needed_from_plan_email_li_wa():
    plan = {
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 2, "channel": "email"},
            {"day": 4, "channel": "whatsapp"},
        ],
        "follow_up": {"enabled": True, "channel": "auto"},
    }
    assert channels_needed_from_sequence_plan(plan) == {"linkedin", "email", "phone"}


def test_channels_needed_only_linkedin():
    plan = {"steps": [{"day": 1, "channel": "linkedin"}]}
    assert channels_needed_from_sequence_plan(plan) == {"linkedin"}


def test_skip_when_plan_channels_already_present():
    p = _prospect(
        linkedin_url="https://www.linkedin.com/in/ana-perez",
        email="ana@acme.com",
    )
    plan = {
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 2, "channel": "email"},
        ]
    }
    with patch(
        "app.services.manual_prospect_channel_enrichment._try_prospeo_enrich"
    ) as mock_prospeo:
        out = enrich_missing_channels(None, p, sequence_plan=plan)
        mock_prospeo.assert_not_called()
    assert out.get("skipped_reason") == "nothing_needed"
    assert out["filled"] == {}


def test_plan_email_wa_li_fills_missing_mail_and_whatsapp():
    """Usuario pone LinkedIn + plan mail/WA/LI → Nexus busca mail y WhatsApp."""
    li = "https://www.linkedin.com/in/ana-perez"
    p = _prospect(linkedin_url=li, email=None, phone=None)
    plan = {
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 2, "channel": "email"},
            {"day": 3, "channel": "whatsapp"},
        ]
    }
    person = {
        "full_name": "Ana Pérez",
        "linkedin_url": li,
        "company_name": "Acme Latam",
        "email": {"email": "ana@acme.com", "revealed": True},
        "mobile": {"mobile": "+5491112345678", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=92,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert out["filled"].get("email") == "ana@acme.com"
    assert out["filled"].get("phone") == "+5491112345678"
    assert "linkedin" not in out["filled"]


def test_linkedin_anchor_fills_email_and_phone_despite_low_confidence():
    """Prospeo suele devolver conf~62 si solo hay LinkedIn; con ancla LI igual enriquecemos."""
    li = "https://www.linkedin.com/in/ana-perez"
    p = _prospect(linkedin_url=li)
    plan = {
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 2, "channel": "email"},
            {"day": 3, "channel": "whatsapp"},
        ]
    }
    person = {
        "full_name": "Ana Pérez",
        "linkedin_url": li,
        "company_name": "Acme Latam",
        "email": {"email": "ana@acme.com", "revealed": False},
        "mobile": {"mobile": "+5491112345678", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=62,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert out["filled"].get("email") == "ana@acme.com"
    assert out["filled"].get("phone") == "+5491112345678"
    assert "linkedin" not in out["filled"]  # ya estaba
    assert p.email == "ana@acme.com"
    assert p.phone == "+5491112345678"
    assert out["missing_after"] == []


def test_does_not_overwrite_user_email():
    li = "https://www.linkedin.com/in/ana-perez"
    p = _prospect(linkedin_url=li, email="user@given.com")
    plan = {"steps": [{"day": 1, "channel": "email"}, {"day": 2, "channel": "whatsapp"}]}
    person = {
        "full_name": "Ana Pérez",
        "linkedin_url": li,
        "email": {"email": "prospeo@acme.com", "revealed": True},
        "mobile": {"mobile": "+5491199999999", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=92,
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert p.email == "user@given.com"
    assert "email" not in out["filled"]
    assert out["filled"].get("phone") == "+5491199999999"


def test_only_searches_channels_needed_by_plan():
    """Plan solo LinkedIn → no pide email/phone aunque falten."""
    p = _prospect(name="Ana Pérez", company_name="Acme Latam")
    plan = {"steps": [{"day": 1, "channel": "linkedin"}]}
    person = {
        "full_name": "Ana Pérez",
        "linkedin_url": "https://www.linkedin.com/in/ana-perez",
        "company_name": "Acme Latam",
        "email": {"email": "ana@acme.com", "revealed": True},
        "mobile": {"mobile": "+5491111111111", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=92,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert out["filled"].get("linkedin")
    assert "email" not in out["filled"]
    assert "phone" not in out["filled"]
    assert p.email is None
    assert p.phone is None


def test_empty_when_prospeo_finds_nothing():
    p = _prospect(linkedin_url="https://www.linkedin.com/in/ana-perez")
    plan = {
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 2, "channel": "email"},
        ]
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value={},
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert out["filled"] == {}
    assert out["missing_after"] == ["email"]
    assert p.email is None


def test_rejects_name_mismatch_without_linkedin_anchor():
    p = _prospect(name="Ana Pérez", company_name="Acme Latam")
    plan = {"steps": [{"day": 1, "channel": "email"}, {"day": 2, "channel": "linkedin"}]}
    person = {
        "full_name": "Carlos Otro",
        "linkedin_url": "https://www.linkedin.com/in/carlos-otro",
        "company_name": "Acme Latam",
        "email": {"email": "carlos@acme.com", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=92,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert out["filled"] == {}
    assert p.email is None


def test_enrich_from_email_only_fills_whatsapp_and_linkedin():
    """Cliente carga solo mail: se buscan WhatsApp y LinkedIn."""
    p = _prospect(
        name="Contacto",
        linkedin_url=None,
        email="ana@acme.com",
        phone=None,
        whatsapp=None,
        company_name="—",
    )
    plan = {
        "steps": [
            {"day": 1, "channel": "email"},
            {"day": 2, "channel": "linkedin"},
            {"day": 3, "channel": "whatsapp"},
        ]
    }
    person = {
        "first_name": "Ana",
        "last_name": "Pérez",
        "company_name": "Acme",
        "mobile": {"mobile": "+5491111111111", "revealed": True},
        "linkedin_url": "https://www.linkedin.com/in/ana-perez",
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ) as enrich_mock,
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=90,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert enrich_mock.call_args.kwargs.get("email") == "ana@acme.com"
    assert out["filled"].get("phone") == "+5491111111111"
    assert "linkedin.com/in/ana-perez" in (out["filled"].get("linkedin") or "")
    assert p.name == "Ana Pérez"
    assert p.whatsapp == "+5491111111111" or p.phone == "+5491111111111"


def test_enrich_from_whatsapp_only_tries_prospeo():
    """Cliente carga solo WhatsApp: se intenta email/LinkedIn."""
    p = _prospect(
        name="Contacto",
        linkedin_url=None,
        email=None,
        phone=None,
        whatsapp="+5491199998888",
        company_name="—",
    )
    plan = {"steps": [{"day": 1, "channel": "email"}, {"day": 2, "channel": "whatsapp"}]}
    person = {
        "first_name": "Ana",
        "last_name": "Pérez",
        "email": {"email": "ana@acme.com", "revealed": True},
        "mobile": {"mobile": "+5491199998888", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ) as enrich_mock,
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=88,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert enrich_mock.call_args.kwargs.get("mobile") == "+5491199998888"
    assert out["filled"].get("email") == "ana@acme.com"
    assert p.name == "Ana Pérez"


def test_name_company_match_accepts_linkedin_at_low_confidence():
    p = _prospect(name="Ana Pérez", company_name="Acme Latam")
    person = {
        "full_name": "Ana Pérez",
        "company_name": "Acme Latam",
        "linkedin_url": "https://www.linkedin.com/in/ana-perez",
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=62,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(
            None,
            p,
            sequence_plan={"steps": [{"channel": "email"}, {"channel": "linkedin"}]},
        )

    assert "linkedin.com/in/ana-perez" in (out["filled"].get("linkedin") or "")


def test_search_person_fallback_when_enrich_empty():
    p = _prospect(name="Ana Pérez", company_name="Acme Latam")
    hit = {
        "person_id": "abc123",
        "full_name": "Ana Pérez",
        "company_name": "Acme Latam",
    }
    detailed = {
        "full_name": "Ana Pérez",
        "company_name": "Acme Latam",
        "linkedin_url": "https://www.linkedin.com/in/ana-perez",
        "email": {"email": "ana@acme.com", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value={},
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp._search_person_raw",
            return_value=([hit], None, None, 200, ""),
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_by_id",
            return_value=detailed,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=92,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(None, p)

    assert out["filled"].get("email") == "ana@acme.com"
    assert "linkedin.com/in/ana-perez" in (out["filled"].get("linkedin") or "")


def test_brave_email_fallback_when_prospeo_empty():
    p = _prospect(
        name="Ana Pérez",
        company_name="Acme Latam",
        linkedin_url="https://www.linkedin.com/in/ana-perez",
    )
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value={},
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_prospeo_search_person",
            return_value={},
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.lead_sourcing.providers.web_search_backends.search_web",
            return_value=[
                (
                    "https://acme.com/equipo",
                    "Ana Pérez — Acme Latam",
                    "Escribile a ana.perez@acme.com",
                )
            ],
        ),
    ):
        out = enrich_missing_channels(None, p)

    assert out["filled"].get("email") == "ana.perez@acme.com"


def test_masked_phone_stripped_and_re_enriched():
    """Teléfono enmascarado de Prospeo no cuenta como canal válido."""
    p = _prospect(
        name="Ana Pérez",
        linkedin_url="https://www.linkedin.com/in/ana-perez",
        email="ana@acme.com",
        phone="+54 9 342 6**-****",
        whatsapp="+54 9 342 6**-****",
        company_name="Acme",
    )
    plan = {"steps": [{"day": 7, "channel": "whatsapp"}]}
    person_search = {
        "person_id": "abc123",
        "mobile": {"mobile": "+54 9 342 6**-****", "revealed": False},
    }
    person_enriched = {
        "person_id": "abc123",
        "mobile": {"mobile": "+5493426123456", "revealed": True},
    }
    with (
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person_search,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_by_id",
            return_value=person_enriched,
        ) as enrich_id,
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=90,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    enrich_id.assert_called_once_with("abc123", require_mobile=True)
    assert out["filled"].get("phone") == "+5493426123456"
    assert p.phone == "+5493426123456"


def test_resolve_person_retries_without_require_mobile_when_empty():
    from app.services.manual_prospect_channel_enrichment import _resolve_person_with_full_mobile

    person = {"person_id": "p1", "full_name": "Ana Pérez"}
    with patch(
        "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_by_id",
        side_effect=[{}, {"person_id": "p1", "email": {"email": "ana@acme.com", "revealed": True}}],
    ) as enrich_id:
        out = _resolve_person_with_full_mobile(person, need_phone=True, need_email=True)

    assert enrich_id.call_count == 2
    assert enrich_id.call_args_list[0].kwargs.get("require_mobile") is True
    assert enrich_id.call_args_list[1].kwargs.get("require_mobile") is False
    assert out.get("email", {}).get("email") == "ana@acme.com" or (
        isinstance(out.get("email"), str) and "ana@" in out["email"]
    )


def test_ensure_company_website_sets_prospect_website():
    from app.services.lead_sourcing.corporate_domain_resolver import CorporateDomainResolution
    from app.services.manual_prospect_channel_enrichment import _ensure_company_website

    p = _prospect(company_website=None, company_name="Acme Latam")
    with patch(
        "app.services.lead_sourcing.corporate_domain_resolver.resolve_corporate_domain_for_company",
        return_value=CorporateDomainResolution(
            "acmelatam.com", "https://acmelatam.com", "web_search", "ok"
        ),
    ):
        web = _ensure_company_website(p, max_seconds=3.0)
    assert web == "https://acmelatam.com"
    assert p.company_website == "https://acmelatam.com"


def test_enrich_resolves_domain_before_prospeo_when_email_missing():
    """Paso 2: sin website, se intenta dominio para mejorar hit rate de mail."""
    p = _prospect(
        linkedin_url="https://www.linkedin.com/in/ana-perez",
        email=None,
        company_website=None,
    )
    plan = {"steps": [{"day": 1, "channel": "email"}]}
    person = {
        "full_name": "Ana Pérez",
        "linkedin_url": p.linkedin_url,
        "company_name": "Acme Latam",
        "email": {"email": "ana@acme.com", "revealed": True},
    }
    with (
        patch(
            "app.services.manual_prospect_channel_enrichment._ensure_company_website",
            return_value="https://acme.com",
        ) as ensure_dom,
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.enrich_person_record",
            return_value=person,
        ),
        patch(
            "app.services.lead_sourcing.providers.prospeo_mvp.confidence_from_person",
            return_value=90,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._search_brave_linkedin_url",
            return_value=None,
        ),
        patch(
            "app.services.manual_prospect_channel_enrichment._try_brave_email",
            return_value={},
        ),
    ):
        out = enrich_missing_channels(None, p, sequence_plan=plan)

    assert ensure_dom.called
    assert out["filled"].get("email") == "ana@acme.com"
