"""Flujo 'Conectar' LinkedIn + cola por acción (connect | message | reply)."""

from unittest.mock import MagicMock

from app.models.prospect import Prospect
from app.services import linkedin_assisted_service as las


def _prospect(**kw) -> Prospect:
    base = dict(
        id=1,
        company_id=1,
        campaign_id=1,
        name="Ada Lovelace",
        company_name="Analytical Engines",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace/",
        status="imported",
        compatibility_score=80,
        interest_probability=50,
    )
    base.update(kw)
    return Prospect(**base)


def _db_no_inbound() -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = None  # sin outbound/inbound previos
    return db


def test_connect_pending_is_queue_eligible_as_connect_action():
    p = _prospect(linkedin_connection_status="invite_pending")
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "connect"
    assert task.connection_status == "invite_pending"
    # La tarea de conexión no expone borrador de mensaje.
    assert task.message == ""


def test_invite_sent_with_draft_is_message_in_queue():
    """Tras Contactar: borrador en Mensajes (humano envía cuando acepte; sin re-sondeo)."""
    p = _prospect(
        linkedin_connection_status="invite_sent",
        linkedin_assisted_draft="Hola, gracias por conectar.",
        linkedin_post_connect_draft_at=__import__("datetime").datetime.now(
            __import__("datetime").UTC
        ),
    )
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "message"


def test_none_with_draft_is_verify_not_message():
    """Sin check de grado: nunca Enviar mensaje (bug campañas grandes)."""
    p = _prospect(
        linkedin_connection_status="none",
        linkedin_assisted_draft="Hola Franco, te escribo por Nexus.",
        linkedin_assist_status="suggested",
    )
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "verify_connect"


def test_heal_none_with_draft_requeues_check():
    from app.services.linkedin_sequence_policy import heal_none_with_linkedin_draft

    p = _prospect(
        linkedin_connection_status="none",
        linkedin_assisted_draft="Hola",
        linkedin_assist_status="suggested",
    )
    assert heal_none_with_linkedin_draft(p) is True
    assert p.linkedin_connection_status == "check_queued"


def test_connected_with_draft_is_message_action():
    p = _prospect(
        linkedin_connection_status="connected",
        linkedin_assisted_draft="Gracias por conectar, Ada.",
    )
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "message"
    assert task.message == "Gracias por conectar, Ada."


def test_queue_touch_none_status_starts_checking():
    p = _prospect(linkedin_connection_status="none")
    campaign = MagicMock()
    action = las.queue_linkedin_sequence_touch(
        _db_no_inbound(), p, campaign, "DM post-aceptación", log_event=False
    )
    assert action == "checking"
    assert p.linkedin_connection_status == "checking"
    assert (p.linkedin_assisted_draft or "").strip() == "DM post-aceptación"


def test_not_connected_without_checking_does_not_enqueue_connect(monkeypatch):
    """Sin pasar por checking, not_connected no inventa Contactar."""
    p = _prospect(linkedin_connection_status="none")
    campaign = MagicMock()
    monkeypatch.setattr(las, "_load_campaign", lambda db, prospect: campaign)
    monkeypatch.setattr(las, "_count_company_checking", lambda db, company_id: 0)
    status, _draft = las.apply_connection_status(_db_no_inbound(), p, "not_connected")
    assert status == "checking"
    assert p.linkedin_connection_status == "checking"


def test_check_failed_is_verify_not_connect():
    p = _prospect(linkedin_connection_status="check_failed")
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "verify_connect"


def test_queue_touch_invite_pending_is_connect():
    from datetime import UTC, datetime

    p = _prospect(
        linkedin_connection_status="invite_pending",
        linkedin_assisted_draft="Hola",
        linkedin_last_assisted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    campaign = MagicMock()
    action = las.queue_linkedin_sequence_touch(
        _db_no_inbound(), p, campaign, "Hola", log_event=False
    )
    assert action == "connect"


def test_mark_connect_sent_schedules_quality_draft(monkeypatch):
    """Al marcar Contactar sin borrador → agenda compose; no stub sync."""
    p = _prospect(
        linkedin_connection_status="invite_pending",
        linkedin_assisted_draft=None,
    )
    campaign = MagicMock()
    scheduled: list[int] = []
    monkeypatch.setattr(las, "_load_campaign", lambda db, prospect: campaign)
    monkeypatch.setattr(las, "_sync_pending_linkedin_touch_body", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "_log_activity", lambda *_a, **_k: None)
    monkeypatch.setattr(
        las, "schedule_linkedin_quality_draft", lambda pid: scheduled.append(pid)
    )

    msg = las.mark_connect_sent(_db_no_inbound(), p)
    assert p.linkedin_connection_status == "invite_sent"
    assert p.linkedin_invite_sent_at is not None
    assert p.linkedin_post_connect_draft_at is not None
    assert not (p.linkedin_assisted_draft or "").strip()
    assert scheduled == [p.id]
    # Tras Contactar puede quedar en Mensajes aunque el borrador aún no esté listo.
    assert las.is_queue_eligible(p) is True
    assert "preparando" in msg.lower() or "cola" in msg.lower()


def test_invite_sent_not_connected_report_stays_invite_sent(monkeypatch):
    """not_connected no saca de invite_sent (sigue esperando aceptación)."""
    p = _prospect(linkedin_connection_status="invite_sent")
    campaign = MagicMock()
    monkeypatch.setattr(las, "_load_campaign", lambda db, prospect: campaign)
    status, _ = las.apply_connection_status(_db_no_inbound(), p, "not_connected")
    assert status == "invite_sent"
    assert p.linkedin_connection_status == "invite_sent"


def test_not_connected_from_check_failed_becomes_invite_pending(monkeypatch):
    """Tras timeout 120s (check_failed), un reporte real de la extensión debe valer."""
    p = _prospect(linkedin_connection_status="check_failed", linkedin_assisted_draft=None)
    campaign = MagicMock()
    campaign.product_id = None
    campaign.product = None
    monkeypatch.setattr(las, "_load_campaign", lambda db, prospect: campaign)
    monkeypatch.setattr(las, "_sync_pending_linkedin_touch_body", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "_log_activity", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "schedule_linkedin_quality_draft", lambda *_a, **_k: None)
    monkeypatch.setattr(
        las,
        "mark_connect_suggested",
        lambda db, pr, c, log_event=False, pending_draft=None: setattr(
            pr, "linkedin_connection_status", "invite_pending"
        ),
    )
    monkeypatch.setattr(
        las, "_queue_ready_linkedin_draft", lambda *_a, **_k: "Hola draft"
    )
    monkeypatch.setattr(las, "promote_next_linkedin_connection_check", lambda *_a, **_k: None)

    status, _ = las.apply_connection_status(_db_no_inbound(), p, "not_connected")
    assert status == "invite_pending"
    assert p.linkedin_connection_status == "invite_pending"


def test_not_connected_from_check_queued_becomes_invite_pending(monkeypatch):
    p = _prospect(linkedin_connection_status="check_queued", linkedin_assisted_draft=None)
    campaign = MagicMock()
    campaign.product_id = None
    campaign.product = None
    monkeypatch.setattr(las, "_load_campaign", lambda db, prospect: campaign)
    monkeypatch.setattr(las, "_sync_pending_linkedin_touch_body", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "_log_activity", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "schedule_linkedin_quality_draft", lambda *_a, **_k: None)
    monkeypatch.setattr(
        las,
        "mark_connect_suggested",
        lambda db, pr, c, log_event=False, pending_draft=None: setattr(
            pr, "linkedin_connection_status", "invite_pending"
        ),
    )
    monkeypatch.setattr(las, "_queue_ready_linkedin_draft", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "promote_next_linkedin_connection_check", lambda *_a, **_k: None)

    status, _ = las.apply_connection_status(_db_no_inbound(), p, "not_connected")
    assert status == "invite_pending"


def test_connected_from_checking_schedules_quality_draft(monkeypatch):
    """1er grado: piso CRM inmediato en Mensajes + upgrade en background."""
    p = _prospect(linkedin_connection_status="checking", linkedin_assisted_draft=None)
    campaign = MagicMock()
    campaign.product_id = None
    campaign.product = None
    campaign.sender_name = "Joaquin"
    scheduled: list[int] = []
    monkeypatch.setattr(las, "_load_campaign", lambda db, prospect: campaign)
    monkeypatch.setattr(las, "_sync_pending_linkedin_touch_body", lambda *_a, **_k: None)
    monkeypatch.setattr(las, "_log_activity", lambda *_a, **_k: None)
    monkeypatch.setattr(
        las, "schedule_linkedin_quality_draft", lambda pid: scheduled.append(pid)
    )
    monkeypatch.setattr(
        las,
        "mark_draft_suggested",
        lambda db, pr, c, d, log_event=False: setattr(pr, "linkedin_assisted_draft", d),
    )

    status, draft = las.apply_connection_status(_db_no_inbound(), p, "connected")
    assert status == "connected"
    assert p.linkedin_connection_status == "connected"
    assert (draft or "").strip()
    assert (p.linkedin_assisted_draft or "").strip()
    assert p.id in scheduled
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "message"


def test_connected_with_quality_draft_is_message_eligible():
    p = _prospect(
        linkedin_connection_status="connected",
        linkedin_assisted_draft=(
            "Hola Ada,\n\nSoy Joaquin. Te escribo por tu rol como CTO en Analytical Engines.\n\n"
            "Habitualmente los equipos pierden horas en prospección. "
            "Ayudamos a agendar más reuniones.\n\n"
            "¿Te queda bien una llamada corta esta semana para ver si aplica a tu equipo?"
        ),
    )
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "message"


def test_connected_with_stub_still_queue_eligible_for_degree_flow():
    """Por ahora la cola prioriza grado 1/2/3; el copy se mejora después."""
    p = _prospect(
        linkedin_connection_status="connected",
        linkedin_assisted_draft="Hola Ada,\n\n¿Tenés 10 minutos para una llamada corta?",
    )
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "message"


def test_connected_without_draft_is_message_eligible():
    p = _prospect(
        linkedin_connection_status="connected",
        linkedin_assisted_draft=None,
    )
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "message"


def test_checking_with_draft_visible_as_verify_action():
    p = _prospect(
        linkedin_connection_status="checking",
        linkedin_assisted_draft="Hola Ada, listo el primer toque.",
    )
    # Con borrador: visible como Verificar (NO como Conectar).
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "verify_connect"
    assert task.connection_status == "checking"
    assert "Hola Ada" in (task.message or "")


def test_checking_without_draft_still_eligible_for_verify():
    """Verify-first: checking sin borrador cuenta para sondeo automático."""
    p = _prospect(linkedin_connection_status="checking", linkedin_assisted_draft=None)
    assert las.is_queue_eligible(p) is True
    task = las.build_task_read(_db_no_inbound(), p)
    assert task.action == "verify_connect"


def test_verify_connect_hidden_from_visible_queue_but_counted(monkeypatch):
    """Revisión automática: no se muestra en la cola; sí cuenta en pending_verify."""
    from app.services import linkedin_assisted_service as las
    import app.services.linkedin_sequence_policy as policy
    import app.services.daily_send_limits as dsl

    checking = _prospect(
        id=26,
        linkedin_connection_status="checking",
        linkedin_assisted_draft="Hola Ivan",
    )
    connected = _prospect(
        id=25,
        name="Dominique",
        linkedin_connection_status="connected",
        linkedin_assisted_draft=(
            "Hola Dom,\n\nSoy Joaquin. Te escribo por tu trabajo en Acme.\n\n"
            "Ayudamos a agendar más reuniones sin prospección manual.\n\n"
            "¿Te queda bien una llamada corta esta semana para ver si aplica?"
        ),
    )
    campaign = MagicMock(seller_id=8, company_id=1)

    class _Scalars:
        def all(self):
            return [checking, connected]

        def first(self):
            return None

    db = MagicMock()
    db.get.return_value = campaign
    db.scalars.return_value = _Scalars()
    db.scalar.return_value = 1  # ya hay 1 checking → no promover cola

    monkeypatch.setattr(policy, "refresh_linkedin_sequence_state", lambda p: [])
    monkeypatch.setattr(dsl, "limit_for", lambda *_a, **_k: 50)
    monkeypatch.setattr(dsl, "remaining", lambda *_a, **_k: 50)
    monkeypatch.setattr(las, "normalize_company_connection_checks", lambda *_a, **_k: False)

    queue = las.build_campaign_queue(db, 5)
    actions = [t.action for t in queue.tasks]
    assert "verify_connect" not in actions
    assert any(t.action in ("message", "reply") for t in queue.tasks)
    assert queue.pending_verify >= 1


def test_queue_touch_invite_sent_prepares_message():
    p = _prospect(linkedin_connection_status="invite_sent")
    campaign = MagicMock()
    action = las.queue_linkedin_sequence_touch(
        _db_no_inbound(), p, campaign, "DM", log_event=False
    )
    assert action == "message"
    assert (p.linkedin_assisted_draft or "").strip() == "DM"


def test_queue_touch_connected_prepares_dm():
    p = _prospect(linkedin_connection_status="connected")
    campaign = MagicMock()
    action = las.queue_linkedin_sequence_touch(
        _db_no_inbound(), p, campaign, "Mensaje post-conexión", log_event=False
    )
    assert action == "message"
    assert (p.linkedin_assisted_draft or "").strip() == "Mensaje post-conexión"


def test_read_connection_status_defaults_to_none():
    p = _prospect()
    assert las.read_connection_status(p) == "none"
