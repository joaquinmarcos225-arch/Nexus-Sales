"""Tests de normalización de cuerpos de email outbound."""

from app.services.outbound_text_normalize import normalize_outbound_email_body


def test_collapses_soft_wraps_inside_paragraph():
    raw = (
        "Hola Ivan,\n"
        "Mi nombre es Joaquin, te hablo desde CostGuard y te contacto\n"
        "porque\n"
        "ayudamos a empresas como la tuya.\n"
        "\n"
        "Nuestra plataforma automatiza entre un 60% y 90% de las\n"
        "tareas\n"
        "manuales de prospección outbound, unificando email, LinkedIn\n"
        "y\n"
        "WhatsApp en un solo flujo con IA para que el equipo solo\n"
        "intervenga\n"
        "con prospectos con interés real.\n"
        "\n"
        "¿Cómo viene tu agenda para coordinar una demo rápida de 10\n"
        "minutos?"
    )
    out = normalize_outbound_email_body(raw)
    assert "contacto\nporque" not in out
    assert "contacto porque" in out
    assert "las tareas manuales" in out
    assert "LinkedIn y WhatsApp" in out
    assert "solo intervenga con" in out
    assert "10 minutos?" in out
    assert out.startswith("Hola Ivan,\nMi nombre")
    assert "\n\n" in out


def test_preserves_paragraph_breaks():
    raw = "Párrafo uno con varias\npalabras.\n\nPárrafo dos."
    out = normalize_outbound_email_body(raw)
    assert out == "Párrafo uno con varias palabras.\n\nPárrafo dos."


def test_joins_hyphenated_line_breaks():
    raw = "prospec-\nción outbound unificada"
    assert normalize_outbound_email_body(raw) == "prospección outbound unificada"


def test_conversation_allows_opening_greeting_only_first_reply():
    from types import SimpleNamespace

    from app.services.outbound_text_normalize import conversation_allows_opening_greeting

    cold = [SimpleNamespace(direction="outbound")]
    assert conversation_allows_opening_greeting(cold) is True

    first_reply_ctx = [
        SimpleNamespace(direction="outbound"),
        SimpleNamespace(direction="inbound"),
    ]
    assert conversation_allows_opening_greeting(first_reply_ctx) is True

    later = first_reply_ctx + [
        SimpleNamespace(direction="outbound"),
        SimpleNamespace(direction="inbound"),
    ]
    assert conversation_allows_opening_greeting(later) is False


def test_strip_opening_greeting():
    from app.services.outbound_text_normalize import (
        apply_opening_greeting_policy,
        strip_opening_greeting,
    )

    assert strip_opening_greeting("Hola Mia,\n\nPerfecto, quedamos.") == "Perfecto, quedamos."
    assert strip_opening_greeting("Hola Mia, listo el cambio.") == "Listo el cambio."
    assert apply_opening_greeting_policy("Hola Mia, dale.", allow_greeting=True).startswith("Hola")
    assert "Hola" not in apply_opening_greeting_policy("Hola Mia, dale.", allow_greeting=False)

