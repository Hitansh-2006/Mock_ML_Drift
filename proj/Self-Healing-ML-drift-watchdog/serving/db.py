"""
DB layer for the drift-watchdog. Uses SQLite by default so Step 1 runs with
zero setup. Swap DATABASE_URL to a Postgres connection string later (e.g.
'postgresql://user:pass@localhost:5432/driftwatchdog') — the rest of the
code is unchanged since it's all through SQLAlchemy.
"""
import os
import time
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, JSON, DateTime
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
    latency_ms = Column(Float, nullable=True)                 # inference time in milliseconds
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
    challenger_latency_ms = Column(Float, nullable=True)     # challenger inference latency in ms
    production_latency_ms = Column(Float, nullable=True)     # production inference latency in ms
    n_labels_compared = Column(Integer, nullable=True)
    status = Column(String, default="pending")  # pending | approved | rejected | withdrawn
    decided_at = Column(Float, nullable=True)
    decided_by = Column(String, nullable=True)
    explanation = Column(String, nullable=True)  # optional LLM-generated plain-English summary


def init_db():
    Base.metadata.create_all(bind=engine)
    # Ensure newly added columns exist in existing SQLite or Postgres tables
    from sqlalchemy import text
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE prediction_logs ADD COLUMN latency_ms FLOAT",
            "ALTER TABLE promotion_requests ADD COLUMN challenger_latency_ms FLOAT",
            "ALTER TABLE promotion_requests ADD COLUMN production_latency_ms FLOAT",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists or table freshly created


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
