"""WhatsApp inbound sync + Meta webhook parsing."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.whatsapp_inbound_sync import (
    _normalize_wa_dedup_compact,
    _wa_dedup_id,
    _wa_texts_look_like_same_message,
    ingest_meta_webhook_messages,
    register_whatsapp_inbound,
    resolve_prospect_by_whatsapp_digits,
)


def test_wa_dedup_collapses_typo_variants():
    a = "Hola, agendame a las 11"
    b = "hola,agendame a las11"
    c = "hola, agendame a oas 11"
    assert _normalize_wa_dedup_compact(a) == _normalize_wa_dedup_compact(b)
    assert _wa_texts_look_like_same_message(a, b)
    assert _wa_texts_look_like_same_message(a, c)
    id_a = _wa_dedup_id(prospect_id=65, text=a, external_id="wa-store:65:abc")
    id_b = _wa_dedup_id(prospect_id=65, text=b, external_id="wa-in:65:def")
    assert id_a == id_b
    assert id_a.startswith("wa-hash:")


def test_wa_dedup_keeps_meta_wamid():
    wid = _wa_dedup_id(prospect_id=1, text="hola", external_id="wamid.ABC123")
    assert wid == "wamid.ABC123"


def test_ingest_meta_text_message_registers():
    prospect = SimpleNamespace(id=9, campaign_id=3, name="Ana", company_id=1)
    campaign = SimpleNamespace(id=3, company_id=1)
    db = MagicMock()
    db.get.side_effect = lambda model, pk: campaign if pk == 3 else None

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5491128942875",
                                    "id": "wamid.TEST123",
                                    "type": "text",
                                    "text": {"body": "Dale, agendemos mañana a las 15"},
                                }
                            ],
                            "contacts": [{"profile": {"name": "Ana"}}],
                        }
                    }
                ]
            }
        ]
    }

    with patch(
        "app.services.whatsapp_inbound_sync.resolve_prospect_by_whatsapp_digits",
        return_value=prospect,
    ), patch(
        "app.services.whatsapp_inbound_sync.register_whatsapp_inbound",
        return_value={"inserted": True, "reply_draft_ready": True},
    ) as reg:
        stats = ingest_meta_webhook_messages(db, payload=payload)

    assert stats["processed"] == 1
    assert stats["inserted"] == 1
    assert stats["unmatched"] == 0
    reg.assert_called_once()
    assert reg.call_args.kwargs["message"] == "Dale, agendemos mañana a las 15"
    assert reg.call_args.kwargs["whatsapp_message_id"] == "wamid.TEST123"


def test_register_ignores_echo_of_our_outbound():
    """Si el 'inbound' es el mismo Day1 que acabamos de mandar, no generar horarios."""
    p = SimpleNamespace(
        id=1,
        campaign_id=1,
        sequence_paused=False,
        whatsapp_assisted_draft="Hola Ana, ¿tenés 10 minutos esta semana para una llamada?",
        whatsapp="+5491112345678",
        phone=None,
    )
    c = SimpleNamespace(id=1, company_id=1)
    db = MagicMock()

    with patch(
        "app.services.whatsapp_inbound_sync.process_whatsapp_inbound_for_prospect",
    ) as proc, patch(
        "app.services.whatsapp_assisted_service.prepare_whatsapp_reply_after_inbound",
    ) as prep:
        result = register_whatsapp_inbound(
            db,
            prospect=p,
            campaign=c,
            message="Hola Ana, ¿tenés 10 minutos esta semana para una llamada?",
        )

    assert result.get("echo_ignored") is True
    assert result["inserted"] is False
    assert result["reply_draft_ready"] is False
    proc.assert_not_called()
    prep.assert_not_called()


def test_register_ignores_truncated_echo_after_draft_cleared():
    """Tras mark-sent el draft se limpia; el preview truncado del outbound sigue siendo eco."""
    from datetime import UTC, datetime

    outbound = SimpleNamespace(
        message=(
            "[WhatsApp · enviado por SDR]\n"
            "Hola Ana, te escribo porque ayudamos a equipos comerciales a consolidar prospectos."
        ),
        created_at=datetime.now(UTC),
        id=99,
    )
    p = SimpleNamespace(
        id=1,
        campaign_id=1,
        sequence_paused=False,
        whatsapp_assisted_draft=None,
        whatsapp="+5491112345678",
        phone=None,
    )
    c = SimpleNamespace(id=1, company_id=1)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [outbound]

    with patch(
        "app.services.whatsapp_inbound_sync.process_whatsapp_inbound_for_prospect",
    ) as proc, patch(
        "app.services.whatsapp_assisted_service.prepare_whatsapp_reply_after_inbound",
    ) as prep:
        result = register_whatsapp_inbound(
            db,
            prospect=p,
            campaign=c,
            message="Hola Ana, te escribo porque ayudamos a equipos comerciales a consolidar prospectos.",
        )

    assert result.get("echo_ignored") is True
    assert result["reply_draft_ready"] is False
    proc.assert_not_called()
    prep.assert_not_called()


def test_ingest_statuses_only_is_noop():
    db = MagicMock()
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [{"id": "wamid.x", "status": "delivered"}],
                            "messages": [],
                        }
                    }
                ]
            }
        ]
    }
    stats = ingest_meta_webhook_messages(db, payload=payload)
    assert stats["processed"] == 0
    assert stats["inserted"] == 0


def test_resolve_prospect_by_digits_matches_variant():
    p = SimpleNamespace(
        id=1,
        whatsapp="+54 9 11 2894-2875",
        phone=None,
        company_id=1,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [p]
    found = resolve_prospect_by_whatsapp_digits(db, company_id=1, from_digits="5491128942875")
    assert found is p


def test_resolve_prospect_interior_ar_without_mobile_9():
    """WA Web a veces reporta 543476… sin el 9; el prospecto puede estar en 5493476…"""
    p = SimpleNamespace(
        id=34,
        whatsapp="+54 9 3476 36-2762",
        phone=None,
        company_id=1,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [p]
    found = resolve_prospect_by_whatsapp_digits(db, company_id=1, from_digits="543476362762")
    assert found is p


def test_resolve_does_not_match_on_last_8_suffix_alone():
    """Dos números distintos que comparten últimos 8 dígitos no deben colisionar."""
    watched = SimpleNamespace(
        id=10,
        whatsapp="+54911558071674",  # …58071674
        phone=None,
        company_id=1,
    )
    other = SimpleNamespace(
        id=20,
        whatsapp="+54911558071674",  # same — control that exact still works
        phone=None,
        company_id=1,
    )
    # Número distinto: mismo "71674" final pero no es el mismo E.164 / variante AR.
    stranger = SimpleNamespace(
        id=99,
        whatsapp="+54111234571674",
        phone=None,
        company_id=1,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [stranger, watched]
    found = resolve_prospect_by_whatsapp_digits(
        db, company_id=1, from_digits="54911558071674"
    )
    assert found is watched

    db2 = MagicMock()
    db2.scalars.return_value.all.return_value = [stranger]
    assert (
        resolve_prospect_by_whatsapp_digits(db2, company_id=1, from_digits="54911558071674")
        is None
    )
    _ = other
