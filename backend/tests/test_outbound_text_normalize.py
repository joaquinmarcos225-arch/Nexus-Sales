"""Tests de normalización de cuerpos de email outbound."""

from app.services.outbound_text_normalize import (
    apply_opening_greeting_policy,
    normalize_outbound_email_body,
    strip_inbound_latiguillo_opener,
    strip_unverified_meeting_claims,
)


def test_buena_pregunta_is_not_treated_as_greeting():
    raw = (
        "Buena pregunta.\n\n"
        "El pricing lo armamos según tamaño del equipo.\n\n"
        "¿Lo vemos en una call corta?"
    )
    out = normalize_outbound_email_body(raw)
    assert "Buena pregunta.," not in out
    # normalize no lo saca (eso es del scrub de réplica); no lo convierte en saludo.
    assert "Buena pregunta" in out


def test_strip_inbound_latiguillo_buena_pregunta():
    raw = (
        "Buena pregunta.\n\n"
        "El pricing lo armamos según tamaño del equipo.\n\n"
        "¿Lo vemos en una call corta?"
    )
    out = strip_inbound_latiguillo_opener(raw)
    assert not out.lower().startswith("buena pregunta")
    assert out.startswith("El pricing")


def test_first_reply_keeps_hola_strips_latiguillo():
    raw = "Hola Ana,\n\nPerfecto.\n\nIntegramos con HubSpot vía API."
    out = apply_opening_greeting_policy(raw, allow_greeting=True)
    assert out.lower().startswith("hola ana")
    assert "perfecto" not in out.split("\n\n")[0].lower() or "Integramos" in out
    assert "Integramos con HubSpot" in out


def test_mid_thread_strips_hola_and_latiguillo():
    raw = "Hola Ana,\n\nGenial. El onboarding suele tomar una semana."
    out = apply_opening_greeting_policy(raw, allow_greeting=False)
    assert not out.lower().startswith("hola")
    assert not out.lower().startswith("genial")
    assert "onboarding" in out.lower()


def test_wall_of_text_splits_only_on_topic_change():
    """Saludo · respuesta (varias oraciones juntas) · CTA. No 1 oración = 1 párrafo."""
    raw = (
        "Hola Test, Nexus Sales ya está en producción con varios equipos de "
        "outbound en LATAM, incluyendo empresas mid-market que gestionan "
        "campañas multicanal activas. No es un piloto; nuestros clientes "
        "automatizan entre 60% y 90% de sus tareas manuales de prospección, "
        "integrando email, LinkedIn y WhatsApp en un flujo único. Esto les "
        "permite que los SDRs intervengan solo cuando hay interés real, "
        "optimizando tiempo y resultados. Si querés, puedo contarte cómo se "
        "implementa en equipos similares al tuyo."
    )
    out = normalize_outbound_email_body(raw)
    parts = [p for p in out.split("\n\n") if p.strip()]
    assert parts[0] == "Hola Test,"
    assert len(parts) == 3
    assert "piloto" in parts[1].lower()
    assert "producción" in parts[1].lower()
    assert "Si querés" in parts[2] or "si querés" in parts[2].lower()
    # El cuerpo de respuesta es UN párrafo (varias oraciones), no partidas.
    assert parts[1].count(". ") >= 2


def test_answer_plus_slots_still_splits_cta_inside_first_block():
    raw = (
        "La implementación con un equipo de 3–4 SDRs suele tomar entre 1 y 2 "
        "semanas para quedar operativa. En el día a día, los SDRs dejan tareas "
        "manuales repetitivas. Si querés, puedo contarte cómo lo adaptamos.\n\n"
        "Estos horarios me quedan libres:\n"
        "• Lunes 09:00\n"
        "• Martes 09:00\n\n"
        "¿Cuál preferís?"
    )
    out = normalize_outbound_email_body(raw)
    parts = [p for p in out.split("\n\n") if p.strip()]
    assert any("Si querés" in p or "si querés" in p.lower() for p in parts)
    assert any("horarios" in p.lower() for p in parts)
    # Respuesta y CTA no van pegados en el mismo párrafo.
    answer_parts = [p for p in parts if "implementación" in p.lower() or "SDRs" in p]
    assert answer_parts
    assert "Si querés" not in answer_parts[0]


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
    assert out.startswith("Hola Ivan,\n\nMi nombre")
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


def test_email_thread_envelope_three_phases():
    from types import SimpleNamespace

    from app.services.outbound_text_normalize import (
        apply_email_thread_envelope,
        resolve_email_thread_phase,
    )

    mid_hist = [
        SimpleNamespace(direction="outbound"),
        SimpleNamespace(direction="inbound"),
        SimpleNamespace(direction="outbound"),
        SimpleNamespace(direction="inbound"),
    ]
    assert resolve_email_thread_phase(mid_hist) == "mid_thread"
    assert (
        resolve_email_thread_phase(mid_hist, is_meeting_confirmation=True)
        == "meeting_confirm"
    )

    mid = apply_email_thread_envelope(
        "Entiendo que preferís ver números antes de agendar.\n\n"
        "¿Qué día y franja te quedan cómodos?",
        phase="mid_thread",
        prospect_name="Test 1",
        seller_name="Joaquín Pérez",
    )
    assert "Hola" not in mid
    assert mid.rstrip().endswith("Saludos,\nJoaquín")

    confirm = apply_email_thread_envelope(
        "Listo Test, te agendé para martes 10:00.\n\n"
        "Te mandé la invitación a equipo@costguard.com.ar.\n\n"
        "Gracias Test, nos vemos ahí.",
        phase="meeting_confirm",
        prospect_name="Test 1",
        seller_name="Joaquín Pérez",
    )
    assert "Hola" not in confirm
    assert confirm.rstrip().endswith("Un saludo,\nJoaquín")
    assert "nos vemos" in confirm.lower()

    start = apply_email_thread_envelope(
        "Te contacto porque automatizamos prospección outbound.",
        phase="thread_start",
        prospect_name="Test 1",
        seller_name="Joaquín",
        company_name="CostGuard",
    )
    assert start.startswith("Hola Test,")
    assert "Saludos,\nJoaquín,\nCostGuard" in start

    demo = apply_email_thread_envelope(
        "El lunes no tengo a las 15:15.\n\nSaludos, Joaquin CostGuard Demo Client",
        phase="thread_start",
        prospect_name="Eduardo",
        seller_name="Joaquin",
        company_name="CostGuard Demo Client",
    )
    assert "Demo" not in demo
    assert "Client" not in demo
    assert "Saludos,\nJoaquin,\nCostGuard" in demo
    assert "Saludos, Joaquin CostGuard" not in demo


def test_strip_unverified_meeting_claims_removes_deferred_link_and_pitch():
    raw = (
        "Quedamos entonces para el martes a las 10. Te enviaré el link de la "
        "reunión un día antes para que lo tengas a mano.\n\n"
        "Mientras tanto, si querés, puedo adelantarte algunos detalles "
        "específicos sobre cómo Contractia puede adaptarse.\n\n"
        "Quedo atento a cualquier consulta que tengas antes del martes."
    )
    out = strip_unverified_meeting_claims(raw)
    assert "enviaré el link" not in out.lower()
    assert "adelantarte" not in out.lower()
    assert "Quedo atento" in out

