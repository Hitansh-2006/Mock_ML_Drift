"""Test setup: a throwaway SQLite DB and a known admin key, set BEFORE the app is imported."""
import os
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_tmp = tempfile.mkdtemp(prefix="drift_watchdog_tests_")
os.environ["DRIFT_WATCHDOG_DB_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ADMIN_API_KEY"] = "test-key-123"

ROOT = Path(__file__).resolve().parent.parent
KEY = "test-key-123"
H = {"X-API-Key": KEY}


def wipe_db():
    """Empty every table (keeps the schema) so each test starts from a blank slate."""
    from serving.db import Base, engine, init_db
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def sample_features():
    """One real stream row as a /predict payload (NaN -> None, like the simulator)."""
    import pandas as pd
    row = pd.read_csv(ROOT / "data" / "stream_data.csv").iloc[0]
    skip = {"SalePrice", "timestep", "drifted"}
    return {c: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v))
            for c, v in row.items() if c not in skip}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """
    A self-contained serving world: blank DB, a temp models/ dir holding copies
    of the real v1 artifacts as v1/v2/v3 (so approve/predict work without ever
    writing to the real models/ or data/), and a `restart()` that simulates a
    process restart: in-memory STATE back to its import-time defaults, then the
    normal startup logic.
    """
    from fastapi.testclient import TestClient
    import serving.app as appmod

    models = tmp_path / "models"
    models.mkdir()
    for version in ("v1", "v2", "v3"):
        for stem, ext in (("model", "joblib"), ("preprocessor", "joblib"), ("model", "meta.json")):
            shutil.copy(ROOT / "models" / f"{stem}_v1.{ext}", models / f"{stem}_{version}.{ext}")
    monkeypatch.setattr(appmod, "MODELS_DIR", models)

    def reset_memory():
        monkeypatch.setitem(appmod.STATE, "production_version", "v1")
        monkeypatch.setitem(appmod.STATE, "shadow_version", None)
        monkeypatch.setitem(appmod.STATE, "models", {})

    def restart():
        reset_memory()
        appmod.startup()

    wipe_db()
    restart()
    # No `with`: lifespan (scheduler restore etc.) is not run, tests drive startup() themselves.
    yield SimpleNamespace(client=TestClient(appmod.app), restart=restart, models=models,
                          features=sample_features(), app=appmod)
    wipe_db()


def add_promotion_request(challenger, production="v1", status="pending", **kw):
    """Insert a promotion request directly (as the evaluation gate would)."""
    from serving.db import SessionLocal, PromotionRequest
    db = SessionLocal()
    try:
        row = PromotionRequest(
            challenger_version=challenger, production_version_at_request=production,
            challenger_mae=kw.get("challenger_mae", 10000.0), production_mae=kw.get("production_mae", 20000.0),
            n_labels_compared=kw.get("n", 50), status=status, created_at=time.time(),
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()
