from app.services.prospect_sequence import (
    CHANNELS_REQUIRED,
    _channels_still_needed,
    _format_channels_requirement_message,
    _format_channels_summary,
)


def test_channels_still_needed():
    assert CHANNELS_REQUIRED == 1
    assert _channels_still_needed(0) == 1
    assert _channels_still_needed(1) == 0
    assert _channels_still_needed(2) == 0
    assert _channels_still_needed(3) == 0


def test_format_channels_requirement_none():
    msg = _format_channels_requirement_message(channel_count=0)
    assert "Falta 1 canal más" in msg
    assert f"0 de {CHANNELS_REQUIRED} requeridos" in msg


def test_format_channels_summary_with_one_channel():
    summary = _format_channels_summary(channel_count=1, available_channels=["email"])
    assert "Detectados: Email" in summary
    assert "mínimo 1 requeridos" in summary
