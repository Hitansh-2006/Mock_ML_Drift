"""
DB layer for the drift-watchdog. Uses SQLite by default so Step 1 runs with
zero setup. Swap DATABASE_URL to a Postgres connection string later (e.g.
'postgresql://user:pass@localhost:5432/driftwatchdog') — the rest of the
code is unchanged since it's all through SQLAlchemy.
"""
import os
import time

from serving import config  # noqa: F401  (loads .env before any setting below is read)
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, JSON, DateTime, Index, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DRIFT_WATCHDOG_DB_URL", "sqlite:///./drift_watchdog.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class PredictionLog(Base):
    """
    One row per prediction served. This is the core table everything else
    (drift monitor, evaluation gate) reads from.
    """
    __tablename__ = "prediction_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestep = Column(Integer, index=True, nullable=True)   # simulator's logical clock
    features = Column(JSON, nullable=False)                  # input feature dict
    prediction = Column(Float, nullable=False)
    model_version = Column(String, nullable=False, index=True)
    is_shadow = Column(Boolean, default=False, index=True)   # shadow model prediction, not served
    true_label = Column(Float, nullable=True)                 # filled in later when label arrives
    latency_ms = Column(Float, nullable=True)                 # inference-only time in ms; NULL for warm-up calls
    # One id per /predict call, shared by the production row and its shadow twin. This (not the
    # optional `timestep`) is how rows are paired, labelled and compared. NULL on legacy rows.
    request_id = Column(String, index=True, nullable=True)
    served_at = Column(Float, default=time.time)


class DriftEvent(Base):
    """
    One row per drift check the monitor runs. Lets you see the drift score
    trend over time, and marks which checks fired an alert.
    """
    __tablename__ = "drift_events"

    id = Column(Integer, primary_key=True, index=True)
    checked_at = Column(Float, default=time.time)
    window_start_timestep = Column(Integer, nullable=True)
    window_end_timestep = Column(Integer, nullable=True)
    drift_score = Column(Float, nullable=False)
    method = Column(String, default="ks_test")
    threshold = Column(Float, nullable=False)
    triggered = Column(Boolean, default=False)
    details = Column(JSON, nullable=True)
    explanation = Column(String, nullable=True)  # optional LLM-generated plain-English summary
    # The window is identified by prediction_logs ids, because timesteps are optional.
    window_start_log_id = Column(Integer, nullable=True)
    window_end_log_id = Column(Integer, nullable=True)
    # Whose training data this window was compared against (the production model's, FR-2).
    reference_version = Column(String, nullable=True)


class PromotionRequest(Base):
    """
    One row per challenger model that passed the evaluation gate and is
    awaiting (or has received) human approval. Nothing in this table ever
    auto-flips a model into production — that only happens via the
    /approve endpoint.
    """
    __tablename__ = "promotion_requests"

    id = Column(Integer, primary_key=True, index=True)
    challenger_version = Column(String, nullable=False)
    production_version_at_request = Column(String, nullable=False)
    created_at = Column(Float, default=time.time)
    challenger_mae = Column(Float, nullable=True)
    production_mae = Column(Float, nullable=True)
    # Inference latency over the paired requests: p50 (median) in *_latency_ms, p95 in *_p95_ms.
    challenger_latency_ms = Column(Float, nullable=True)
    production_latency_ms = Column(Float, nullable=True)
    challenger_latency_p95_ms = Column(Float, nullable=True)
    production_latency_p95_ms = Column(Float, nullable=True)
    n_labels_compared = Column(Integer, nullable=True)
    status = Column(String, default="pending")  # pending | approved | rejected | withdrawn
    decided_at = Column(Float, nullable=True)
    decided_by = Column(String, nullable=True)
    explanation = Column(String, nullable=True)  # optional LLM-generated plain-English summary


class ModelRegistry(Base):
    """
    The DB is the source of truth for which model serves production and which
    one (if any) runs in shadow; the in-memory STATE in serving/app.py is only a
    cache of this table for the /predict hot path. Roles:
      production | shadow | archived | rejected
    The partial unique indexes make "at most one production, at most one
    shadow" a database guarantee, not just a convention in code.
    """
    __tablename__ = "model_registry"

    version = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    created_at = Column(Float, default=time.time)
    promoted_at = Column(Float, nullable=True)       # set when the version became production
    trained_on_rows = Column(Integer, nullable=True)  # from model_<v>.meta.json when present
    metrics = Column(JSON, nullable=True)             # the same meta file, verbatim
    reference_path = Column(String, nullable=True)    # reserved for per-version drift references (FR-2)

    __table_args__ = (
        Index("uq_model_registry_one_production", "role", unique=True,
              sqlite_where=text("role = 'production'"), postgresql_where=text("role = 'production'")),
        Index("uq_model_registry_one_shadow", "role", unique=True,
              sqlite_where=text("role = 'shadow'"), postgresql_where=text("role = 'shadow'")),
    )


class User(Base):
    """A dashboard account. Passwords are only ever stored as salted PBKDF2 hashes."""
    __tablename__ = "users"

    username = Column(String, primary_key=True)   # stored lower-case
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)          # admin | viewer (read-only)
    created_at = Column(Float, default=time.time)


class AuthSession(Base):
    """A login session. Only the SHA-256 of the bearer token is stored, so a DB leak is not a session leak."""
    __tablename__ = "auth_sessions"

    token_hash = Column(String, primary_key=True)
    username = Column(String, nullable=False)
    role = Column(String, nullable=False)
    is_guest = Column(Boolean, default=False, nullable=False)
    created_at = Column(Float, default=time.time)
    expires_at = Column(Float, nullable=False, index=True)


class AutomationState(Base):
    """
    One row (id=1): whether the dashboard's automation loop is switched on, its
    interval and a summary of the last cycle, so the loop survives a restart.
    """
    __tablename__ = "automation_state"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, default=False, nullable=False)
    interval_seconds = Column(Integer, nullable=True)
    started_at = Column(Float, nullable=True)
    last_cycle_at = Column(Float, nullable=True)
    last_cycle = Column(JSON, nullable=True)


# Columns added after the first release. `create_all` only creates MISSING TABLES, never
# missing columns, so a database from an older version needs these added in place.
ADDITIVE_COLUMNS = [
    ("prediction_logs", "latency_ms", "FLOAT"),
    ("prediction_logs", "request_id", "VARCHAR"),
    ("drift_events", "explanation", "VARCHAR"),
    ("drift_events", "window_start_log_id", "INTEGER"),
    ("drift_events", "window_end_log_id", "INTEGER"),
    ("drift_events", "reference_version", "VARCHAR"),
    ("promotion_requests", "explanation", "VARCHAR"),
    ("promotion_requests", "challenger_latency_ms", "FLOAT"),
    ("promotion_requests", "production_latency_ms", "FLOAT"),
    ("promotion_requests", "challenger_latency_p95_ms", "FLOAT"),
    ("promotion_requests", "production_latency_p95_ms", "FLOAT"),
]
ADDITIVE_INDEXES = [
    ("ix_prediction_logs_request_id", "prediction_logs", "request_id"),
]


def migrate(bind=None):
    """
    Bring an existing database up to the current schema. Safe to run on every start, on
    SQLite and Postgres: it INSPECTS what is there and only adds what is missing, each change
    in its own transaction, so one failure can't leave a Postgres transaction aborted and
    silently break the changes after it (the old "try ALTER, ignore errors" loop did).
    """
    from sqlalchemy import inspect, text
    bind = bind or engine
    Base.metadata.create_all(bind=bind)
    existing = {}
    inspector = inspect(bind)
    for table, column, sql_type in ADDITIVE_COLUMNS:
        if table not in existing:
            existing[table] = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing[table]:
            with bind.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
            existing[table].add(column)
    for name, table, column in ADDITIVE_INDEXES:
        with bind.begin() as conn:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def init_db():
    migrate(engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
