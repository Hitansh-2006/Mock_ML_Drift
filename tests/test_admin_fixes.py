"""
Regression tests for the five blocking issues:
  1. python-multipart missing from requirements.txt
  2. /admin/* had no auth and CORS was open to every website
  3. Upload Data accepted anything and destroyed stream_data.csv
  4. Train Baseline overwrote the production model without approval
  5. Retrain could be double-triggered (same version number, concurrent runs)
"""
import threading
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import serving.app as appmod
from serving.app import app
from retrain import retrain_pipeline

ROOT = Path(__file__).resolve().parent.parent
KEY = "test-key-123"
H = {"X-API-Key": KEY}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", KEY)
    monkeypatch.delenv("ADMIN_AUTH_DISABLED", raising=False)


# ---------------------------------------------------------------- 1. dependency
def test_requirements_include_python_multipart():
    assert "python-multipart" in (ROOT / "requirements.txt").read_text().lower()


# ---------------------------------------------------------------- 2. auth + CORS
def _admin_routes():
    """(method, path) for every /admin route, read from the OpenAPI schema so nothing can hide."""
    return [(m.upper(), path) for path, ops in app.openapi()["paths"].items()
            if path.startswith("/admin") for m in ops]


def test_every_admin_route_rejects_requests_without_a_key(client):
    routes = _admin_routes()
    assert len(routes) >= 10  # guards against routes silently escaping the protected router
    for method, path in routes:
        assert client.request(method, path).status_code == 401, f"{method} {path} is unprotected"


def test_wrong_key_rejected_right_key_accepted(client):
    assert client.get("/admin/stats", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/admin/stats", headers=H).status_code == 200


def test_status_and_predict_stay_public(client):
    assert client.get("/status").status_code == 200


def test_admin_api_is_locked_when_no_key_configured(client, monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY")
    assert client.get("/admin/stats", headers=H).status_code == 503


def test_placeholder_key_is_treated_as_unset(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "change-me")
    assert client.get("/admin/stats", headers={"X-API-Key": "change-me"}).status_code == 503


def test_explicit_dev_opt_out(client, monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY")
    monkeypatch.setenv("ADMIN_AUTH_DISABLED", "1")
    assert client.get("/admin/stats").status_code == 200


def test_cors_blocks_foreign_origin_and_allows_dashboard(client):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-api-key"}
    evil = client.options("/admin/retrain", headers={"Origin": "https://evil.example", **pre})
    assert evil.headers.get("access-control-allow-origin") is None
    ok = client.options("/admin/retrain", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cross_site_simple_post_cannot_trigger_actions(client):
    r = client.post("/admin/retrain", headers={"Origin": "https://evil.example"})
    assert r.status_code == 401


# ---------------------------------------------------------------- 3. upload
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    real_train = pd.read_csv(ROOT / "data" / "training_data.csv")
    real_train.head(5).to_csv(tmp_path / "training_data.csv", index=False)
    (tmp_path / "stream_data.csv").write_text("ORIGINAL-STREAM")
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(appmod, "TRAINING_PATH", tmp_path / "training_data.csv")
    return tmp_path


def _good_stream(n=5):
    return pd.read_csv(ROOT / "data" / "stream_data.csv").head(n)


def _upload(client, data: bytes, name="up.csv"):
    return client.post("/admin/upload_stream", headers=H, files={"file": (name, data, "text/csv")})


def _assert_untouched(data_dir):
    assert (data_dir / "stream_data.csv").read_text() == "ORIGINAL-STREAM"
    assert not list(data_dir.glob(".stream_upload_*")), "temp file left behind"


@pytest.mark.parametrize("payload", [
    b"this is not a csv\x00\x01 garbage",
    b"",
    b"   \n  ",
])
def test_garbage_or_empty_upload_is_rejected_and_original_survives(client, data_dir, payload):
    assert _upload(client, payload).status_code == 400
    _assert_untouched(data_dir)


def test_header_only_csv_rejected(client, data_dir):
    header = ",".join(_good_stream().columns).encode()
    assert _upload(client, header + b"\n").status_code == 400
    _assert_untouched(data_dir)


def test_missing_columns_rejected_with_clear_message(client, data_dir):
    r = _upload(client, b"a,b\n1,2\n3,4\n")
    assert r.status_code == 400 and "missing" in r.json()["detail"]
    _assert_untouched(data_dir)


def test_duplicate_timesteps_rejected(client, data_dir):
    df = _good_stream(); df.loc[1, "timestep"] = df.loc[0, "timestep"]
    assert _upload(client, df.to_csv(index=False).encode()).status_code == 400
    _assert_untouched(data_dir)


def test_non_numeric_label_rejected(client, data_dir):
    df = _good_stream().astype({"SalePrice": object}); df.loc[0, "SalePrice"] = "lots"
    assert _upload(client, df.to_csv(index=False).encode()).status_code == 400
    _assert_untouched(data_dir)


def test_oversized_upload_rejected(client, data_dir, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_UPLOAD_BYTES", 10)
    assert _upload(client, _good_stream().to_csv(index=False).encode()).status_code == 413
    _assert_untouched(data_dir)


def test_valid_upload_replaces_file_and_keeps_a_backup(client, data_dir):
    r = _upload(client, _good_stream(5).to_csv(index=False).encode())
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == 5 and body["backup"] == "stream_data.bak.csv"
    assert (data_dir / "stream_data.bak.csv").read_text() == "ORIGINAL-STREAM"
    assert len(pd.read_csv(data_dir / "stream_data.csv")) == 5
    assert not list(data_dir.glob(".stream_upload_*"))


def test_upload_without_drifted_column_is_fine_and_extras_are_reported(client, data_dir):
    df = _good_stream().drop(columns=["drifted"]); df["Notes"] = "x"
    r = _upload(client, df.to_csv(index=False).encode())
    assert r.status_code == 200 and r.json()["extra_columns"] == ["Notes"]


# ---------------------------------------------------------------- 4. train baseline
def _stub_baseline(models_dir):
    for n in ("model_v1.joblib", "preprocessor_v1.joblib", "model_v1.meta.json"):
        (models_dir / n).write_bytes(b"PRODUCTION-BASELINE")


def test_train_baseline_never_overwrites_an_existing_baseline(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "MODELS_DIR", tmp_path)
    _stub_baseline(tmp_path)
    called = []
    import models.train_baseline as tb
    monkeypatch.setattr(tb, "train", lambda *a, **k: called.append(1))
    r = client.post("/admin/train_baseline", headers=H)
    assert r.status_code == 409 and not called
    assert all((tmp_path / n).read_bytes() == b"PRODUCTION-BASELINE"
               for n in ("model_v1.joblib", "preprocessor_v1.joblib", "model_v1.meta.json"))
    assert client.get("/status").json()["baseline_ready"] is True


def test_train_baseline_creates_first_model_when_none_exists(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(appmod, "load_model", lambda v: None)
    import models.train_baseline as tb
    monkeypatch.setattr(tb, "train", lambda version: _stub_baseline(tmp_path))
    assert client.get("/status").json()["baseline_ready"] is False
    r = client.post("/admin/train_baseline", headers=H)
    assert r.status_code == 200
    assert client.get("/status").json()["baseline_ready"] is True
    assert not appmod.TRAIN_LOCK.locked()


def test_train_baseline_rejects_concurrent_run(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "MODELS_DIR", tmp_path)
    assert appmod.TRAIN_LOCK.acquire(blocking=False)
    try:
        assert client.post("/admin/train_baseline", headers=H).status_code == 409
    finally:
        appmod.TRAIN_LOCK.release()


def test_predict_returns_503_not_a_crash_without_a_baseline(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "MODELS_DIR", tmp_path)
    monkeypatch.setitem(appmod.STATE, "models", {})
    assert client.post("/predict", json={"features": {"OverallQual": 5}}).status_code == 503


# ---------------------------------------------------------------- 5. retrain
def test_retrain_refused_while_a_challenger_is_in_shadow(client, monkeypatch):
    monkeypatch.setitem(appmod.STATE, "shadow_version", "v9")
    r = client.post("/admin/retrain", headers=H)
    assert r.status_code == 409 and "v9" in r.json()["detail"]


def test_retrain_refused_while_another_retrain_is_running(client):
    assert appmod.RETRAIN_LOCK.acquire(blocking=False)
    try:
        assert client.post("/admin/retrain", headers=H).status_code == 409
    finally:
        appmod.RETRAIN_LOCK.release()


def test_retrain_runs_once_and_releases_the_lock_even_if_it_fails(client, monkeypatch):
    calls = []
    def fake(n_recent):
        calls.append(n_recent)
        if len(calls) == 1:
            raise RuntimeError("boom")
    monkeypatch.setattr(retrain_pipeline, "run_retrain", fake)
    assert client.post("/admin/retrain", headers=H).status_code == 200   # fails inside, lock must still be freed
    assert not appmod.RETRAIN_LOCK.locked()
    assert client.post("/admin/retrain", headers=H).status_code == 200
    assert calls == [500, 500]


def test_version_numbers_are_reserved_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(retrain_pipeline, "MODELS_DIR", tmp_path)
    (tmp_path / "model_v1.joblib").write_bytes(b"x")
    (tmp_path / "model_v4.joblib").write_bytes(b"x")
    v_a, res_a = retrain_pipeline.reserve_next_version()
    v_b, res_b = retrain_pipeline.reserve_next_version()      # while the first is still held
    assert (v_a, v_b) == ("v5", "v6")
    res_a.unlink(); (tmp_path / "model_v5.joblib").write_bytes(b"x")  # first run finished
    v_c, _ = retrain_pipeline.reserve_next_version()
    assert v_c == "v7"                                          # v5 (finished) and v6 (held) both stay taken


def test_concurrent_retrains_never_get_the_same_version(tmp_path, monkeypatch):
    monkeypatch.setattr(retrain_pipeline, "MODELS_DIR", tmp_path)
    got, barrier = [], threading.Barrier(8)
    def worker():
        barrier.wait()
        got.append(retrain_pipeline.reserve_next_version()[0])
    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(set(got)) == 8


def test_retrain_pipeline_sends_the_admin_key(monkeypatch):
    seen = {}
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"shadow_version": "v5"}
    monkeypatch.setattr(retrain_pipeline.requests, "post",
                        lambda url, **kw: seen.update(kw) or R())
    retrain_pipeline.deploy_shadow("v5")
    assert seen["headers"] == {"X-API-Key": KEY}
