from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.database.base import Base
from app.database.config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_on_connect(dbapi_connection, _connection_record) -> None:
        """WAL + busy_timeout: lecturas del dashboard no bloquean por jobs del scheduler."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Crea todas las tablas registradas en Base.metadata."""
    import app.models  # noqa: F401 — registrar modelos
    Base.metadata.create_all(bind=engine)
    _apply_sqlite_light_migrations(engine)


def _apply_sqlite_light_migrations(bind) -> None:
    """ALTER mínimos para bases locales existentes (SQLite)."""
    insp = inspect(bind)
    try:
        dialect = insp.engine.dialect.name
    except AttributeError:
        dialect = ""

    if dialect != "sqlite":
        return

    with bind.connect() as conn:
        tbl = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        names = {r[0] for r in tbl}
        cols = {}

        def _cols(table: str) -> set[str]:
            if table not in cols:
                cols[table] = {c["name"] for c in insp.get_columns(table)}
            return cols[table]

        if "campaigns" in names and "allowed_channels" not in _cols("campaigns"):
            conn.execute(
                text(
                    """
                    ALTER TABLE campaigns ADD COLUMN allowed_channels TEXT
                    DEFAULT '["linkedin","email","whatsapp"]'
                    """
                )
            )

        campaign_new_cols = [
            ("autopilot_status", "TEXT NOT NULL DEFAULT 'off'"),
            ("autopilot_last_cycle_at", "TEXT"),
            ("autopilot_last_cycle_summary", "TEXT"),
        ]
        if "campaigns" in names:
            for col_name, col_def in campaign_new_cols:
                if col_name not in _cols("campaigns"):
                    conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col_name} {col_def}"))
                    cols.pop("campaigns", None)

        if "outreach_tasks" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE outreach_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        campaign_id INTEGER NOT NULL,
                        prospect_id INTEGER,
                        task_kind VARCHAR(64) NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        notes TEXT,
                        due_at TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        FOREIGN KEY (campaign_id) REFERENCES campaigns (id),
                        FOREIGN KEY (prospect_id) REFERENCES prospects (id)
                    )
                    """
                )
            )

        prospect_new_cols = [
            ("outreach_touch_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_outbound_at", "TEXT"),
            ("last_inbound_at", "TEXT"),
            ("objection_type", "TEXT"),
            ("objection_detected_at", "TEXT"),
            ("interest_level", "TEXT NOT NULL DEFAULT 'low'"),
            ("meeting_nudge_sent_at", "TEXT"),
            ("followup_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_followup_at", "TEXT"),
            ("score_reason", "TEXT"),
            ("next_best_action", "TEXT"),
            ("pipeline_stage", "TEXT NOT NULL DEFAULT 'nuevo'"),
            ("meeting_suggestion_pending", "INTEGER NOT NULL DEFAULT 0"),
        ]
        if "prospects" in names:
            for col_name, col_def in prospect_new_cols:
                if col_name not in _cols("prospects"):
                    conn.execute(text(f"ALTER TABLE prospects ADD COLUMN {col_name} {col_def}"))
                    cols.pop("prospects", None)

        prospect_extra = [
            ("preferred_channel", "TEXT"),
            ("channel_reason", "TEXT"),
            ("linkedin_assisted_draft", "TEXT"),
            ("linkedin_assist_status", "TEXT"),
            ("linkedin_assist_session_id", "TEXT"),
            ("linkedin_last_assisted_at", "TEXT"),
            ("linkedin_sdr_marked_sent_at", "TEXT"),
            ("whatsapp", "TEXT"),
            ("company_website", "TEXT"),
            ("source_provider", "TEXT"),
            ("source_external_id", "TEXT"),
        ]
        if "prospects" in names:
            for col_name, col_def in prospect_extra:
                if col_name not in _cols("prospects"):
                    conn.execute(text(f"ALTER TABLE prospects ADD COLUMN {col_name} {col_def}"))
                    cols.pop("prospects", None)

        if "campaigns" in names and "icp_ai_last_analysis" not in _cols("campaigns"):
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN icp_ai_last_analysis TEXT"))
            cols.pop("campaigns", None)

        if "lead_sourcing_pipelines" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE lead_sourcing_pipelines (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        campaign_id INTEGER NOT NULL UNIQUE,
                        stage VARCHAR(32) NOT NULL DEFAULT 'idle',
                        companies_json TEXT,
                        people_json TEXT,
                        meta_json TEXT,
                        fit_threshold INTEGER NOT NULL DEFAULT 70,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (campaign_id) REFERENCES campaigns (id)
                    )
                    """
                )
            )

        if "meetings" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE meetings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        campaign_id INTEGER NOT NULL,
                        prospect_id INTEGER NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        description TEXT,
                        scheduled_for TEXT NOT NULL,
                        meeting_status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        timezone VARCHAR(128) NOT NULL DEFAULT 'America/Argentina/Buenos_Aires',
                        suggested_slots TEXT,
                        duration_minutes INTEGER NOT NULL DEFAULT 30,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        FOREIGN KEY (campaign_id) REFERENCES campaigns (id),
                        FOREIGN KEY (prospect_id) REFERENCES prospects (id)
                    )
                    """
                )
            )

        if "campaigns" in names and "outreach_activity_log" not in _cols("campaigns"):
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN outreach_activity_log TEXT"))
            cols.pop("campaigns", None)

        prospect_seq_cols = [
            ("sequence_started_at", "TEXT"),
            ("sequence_group", "TEXT NOT NULL DEFAULT 'contactado'"),
            ("sequence_state", "TEXT NOT NULL DEFAULT 'sin_respuesta'"),
            ("sequence_fired_milestones", "TEXT NOT NULL DEFAULT '[]'"),
            ("sequence_paused", "INTEGER NOT NULL DEFAULT 0"),
            ("reactivation_sent_at", "TEXT"),
            ("defer_resume_at", "TEXT"),
        ]
        if "prospects" in names:
            for col_name, col_def in prospect_seq_cols:
                if col_name not in _cols("prospects"):
                    conn.execute(text(f"ALTER TABLE prospects ADD COLUMN {col_name} {col_def}"))
                    cols.pop("prospects", None)

        if "prospects" in names and "gmail_thread_id" not in _cols("prospects"):
            conn.execute(text("ALTER TABLE prospects ADD COLUMN gmail_thread_id VARCHAR(64)"))
            cols.pop("prospects", None)

        if "outreach_messages" in names and "gmail_message_id" not in _cols("outreach_messages"):
            conn.execute(text("ALTER TABLE outreach_messages ADD COLUMN gmail_message_id VARCHAR(128)"))
            cols.pop("outreach_messages", None)

        idx_gmail = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_om_prospect_gmail_msg'"
            )
        ).fetchone()
        if "outreach_messages" in names and idx_gmail is None:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_om_prospect_gmail_msg ON outreach_messages "
                    "(prospect_id, gmail_message_id) WHERE gmail_message_id IS NOT NULL"
                )
            )

        if "meetings" in names:
            for col_name, col_def in (
                ("google_calendar_event_id", "VARCHAR(256)"),
                ("google_calendar_html_link", "VARCHAR(2048)"),
                ("creation_method", "VARCHAR(32) NOT NULL DEFAULT 'manual'"),
                ("created_by_user_id", "INTEGER"),
            ):
                if col_name not in _cols("meetings"):
                    conn.execute(text(f"ALTER TABLE meetings ADD COLUMN {col_name} {col_def}"))
                    cols.pop("meetings", None)

        idx_cal_ev = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_meetings_google_calendar_event_id'"
            )
        ).fetchone()
        if "meetings" in names and idx_cal_ev is None:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_meetings_google_calendar_event_id ON meetings "
                    "(google_calendar_event_id) WHERE google_calendar_event_id IS NOT NULL"
                )
            )

        if "connected_accounts" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE connected_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        provider VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'not_connected',
                        external_email VARCHAR(255),
                        access_token_encrypted TEXT,
                        refresh_token_encrypted TEXT,
                        connected_at TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        FOREIGN KEY (user_id) REFERENCES users (id),
                        UNIQUE (company_id, user_id, provider)
                    )
                    """
                )
            )

        campaign_editor_cols = [
            ("updated_at", "TEXT"),
            ("sender_name", "VARCHAR(255)"),
            ("sender_email", "VARCHAR(255)"),
            ("ai_context", "TEXT"),
            ("followup_delay_days", "INTEGER"),
            ("max_auto_followups", "INTEGER"),
        ]
        if "campaigns" in names:
            for col_name, col_def in campaign_editor_cols:
                if col_name not in _cols("campaigns"):
                    conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col_name} {col_def}"))
                    cols.pop("campaigns", None)

        campaign_automation_cols = [
            ("outreach_email_mode", "TEXT NOT NULL DEFAULT 'draft_only'"),
            ("automation_paused", "INTEGER NOT NULL DEFAULT 0"),
            ("inbound_reply_mode", "TEXT NOT NULL DEFAULT 'draft_only'"),
            ("inbound_reply_delay_minutes", "INTEGER NOT NULL DEFAULT 2"),
        ]
        if "campaigns" in names:
            for col_name, col_def in campaign_automation_cols:
                if col_name not in _cols("campaigns"):
                    conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col_name} {col_def}"))
                    cols.pop("campaigns", None)

        if "inbound_auto_reply_receipts" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE inbound_auto_reply_receipts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        campaign_id INTEGER NOT NULL,
                        prospect_id INTEGER NOT NULL,
                        inbound_gmail_message_id VARCHAR(128) NOT NULL,
                        outcome VARCHAR(24) NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        FOREIGN KEY (campaign_id) REFERENCES campaigns (id),
                        FOREIGN KEY (prospect_id) REFERENCES prospects (id),
                        UNIQUE (prospect_id, inbound_gmail_message_id)
                    )
                    """
                )
            )

        if "ai_decision_events" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE ai_decision_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        campaign_id INTEGER,
                        prospect_id INTEGER,
                        event_type VARCHAR(64) NOT NULL,
                        decision VARCHAR(64) NOT NULL,
                        summary TEXT NOT NULL,
                        payload TEXT,
                        confidence REAL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        FOREIGN KEY (campaign_id) REFERENCES campaigns (id),
                        FOREIGN KEY (prospect_id) REFERENCES prospects (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ai_decision_events_company "
                    "ON ai_decision_events (company_id, created_at DESC)"
                )
            )

        if "companies" in names and "global_automation_stop" not in _cols("companies"):
            conn.execute(
                text("ALTER TABLE companies ADD COLUMN global_automation_stop INTEGER NOT NULL DEFAULT 0")
            )

        if "campaigns" in names and "automation_mode" not in _cols("campaigns"):
            conn.execute(
                text(
                    "ALTER TABLE campaigns ADD COLUMN automation_mode "
                    "TEXT NOT NULL DEFAULT 'semi_auto'"
                )
            )

        if "prospects" in names and "ai_paused" not in _cols("prospects"):
            conn.execute(text("ALTER TABLE prospects ADD COLUMN ai_paused INTEGER NOT NULL DEFAULT 0"))

        user_auth_cols = [
            ("first_name", "VARCHAR(128) NOT NULL DEFAULT ''"),
            ("last_name", "VARCHAR(128) NOT NULL DEFAULT ''"),
            ("password_hash", "VARCHAR(255)"),
            ("is_active", "INTEGER NOT NULL DEFAULT 1"),
            ("updated_at", "TEXT"),
        ]
        if "users" in names:
            for col_name, col_def in user_auth_cols:
                if col_name not in _cols("users"):
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                    cols.pop("users", None)

        if "companies" in names:
            for col_name, col_def in (
                ("plan", "VARCHAR(64) NOT NULL DEFAULT 'starter'"),
                ("updated_at", "TEXT"),
            ):
                if col_name not in _cols("companies"):
                    conn.execute(text(f"ALTER TABLE companies ADD COLUMN {col_name} {col_def}"))
                    cols.pop("companies", None)

        prospect_owner_cols = [
            ("owner_user_id", "INTEGER"),
            ("ownership_status", "VARCHAR(32) NOT NULL DEFAULT 'libre'"),
            ("claimed_at", "TEXT"),
            ("sequence_completed_at", "TEXT"),
            ("ownership_cooldown_until", "TEXT"),
            ("previous_owner_user_id", "INTEGER"),
        ]
        if "prospects" in names:
            for col_name, col_def in prospect_owner_cols:
                if col_name not in _cols("prospects"):
                    conn.execute(text(f"ALTER TABLE prospects ADD COLUMN {col_name} {col_def}"))
                    cols.pop("prospects", None)

        if "prospect_ownership_events" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE prospect_ownership_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        prospect_id INTEGER NOT NULL,
                        actor_user_id INTEGER,
                        from_user_id INTEGER,
                        to_user_id INTEGER,
                        action VARCHAR(64) NOT NULL,
                        note TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        FOREIGN KEY (prospect_id) REFERENCES prospects (id),
                        FOREIGN KEY (actor_user_id) REFERENCES users (id),
                        FOREIGN KEY (from_user_id) REFERENCES users (id),
                        FOREIGN KEY (to_user_id) REFERENCES users (id)
                    )
                    """
                )
            )

        conn.execute(text("UPDATE users SET role = 'sdr' WHERE role = 'seller'"))
        conn.execute(text("UPDATE users SET role = 'gerente' WHERE role = 'admin'"))
        conn.execute(
            text(
                "UPDATE users SET first_name = TRIM(SUBSTR(name, 1, INSTR(name || ' ', ' ') - 1)) "
                "WHERE (first_name IS NULL OR first_name = '') AND name IS NOT NULL"
            )
        )

        if "automation_job_state" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE automation_job_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_key VARCHAR(128) NOT NULL UNIQUE,
                        locked_until TEXT,
                        last_started_at TEXT,
                        last_finished_at TEXT,
                        last_success_at TEXT,
                        last_error TEXT,
                        last_result_meta TEXT,
                        run_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        if "teams" not in names:
            conn.execute(
                text(
                    """
                    CREATE TABLE teams (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_id INTEGER NOT NULL,
                        name VARCHAR(128) NOT NULL,
                        description TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (company_id) REFERENCES companies (id),
                        UNIQUE (company_id, name)
                    )
                    """
                )
            )

        if "users" in names and "team_id" not in _cols("users"):
            conn.execute(text("ALTER TABLE users ADD COLUMN team_id INTEGER REFERENCES teams(id)"))

        prospect_seq_cols = [
            ("sequence_playbook_draft", "TEXT"),
            ("sequence_touch_log", "TEXT"),
            ("playbook_name", "VARCHAR(128)"),
            ("next_touch_at", "TEXT"),
            ("commercial_state", "VARCHAR(32) NOT NULL DEFAULT 'prospeccion'"),
            ("commercial_state_is_testing", "INTEGER NOT NULL DEFAULT 0"),
            ("conversation_state", "VARCHAR(32) NOT NULL DEFAULT 'sin_conversacion'"),
        ]
        if "prospects" in names:
            for col_name, col_def in prospect_seq_cols:
                if col_name not in _cols("prospects"):
                    conn.execute(text(f"ALTER TABLE prospects ADD COLUMN {col_name} {col_def}"))
                    cols.pop("prospects", None)

        if "outreach_messages" in names and "is_testing" not in _cols("outreach_messages"):
            conn.execute(
                text("ALTER TABLE outreach_messages ADD COLUMN is_testing INTEGER NOT NULL DEFAULT 0")
            )

        conn.commit()


def get_db() -> Generator[Session, None, None]:
    """Dependencia FastAPI para sesiones de BD."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
