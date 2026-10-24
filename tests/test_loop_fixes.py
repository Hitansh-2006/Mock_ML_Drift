"""
The self-healing loop's correctness bugs.

  BUG-3  pairing by request_id (timestep is optional metadata; real traffic has none)
  BUG-2  drift is measured against the CURRENT production model's training data
  BUG-8  latency: inference only (never model loading), warm-up discarded, p50/p95
  BUG-9  stats per production version; thresholds from config / the model's own validation error
  plus the additive DB migration these need (BUG-6's safe form).
"""
import time

import numpy as np
import pandas as pd
import pytest

from tests.conftest import H, ROOT, add_promotion_request

STREAM = pd.read_csv(ROOT / "data" / "stream_data.csv")


# ------------------------------------------------------------------ helpers
def add_log(**kw):
    from serving.db import SessionLocal, PredictionLog
    db = SessionLocal()
    try:
        row = PredictionLog(**{"features": {}, "prediction": 100.0, "model_version": "v1", "is_shadow": False, **kw})
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def logs(**filters):
    from serving.db import SessionLocal, PredictionLog
    db = SessionLocal()
    try:
        return db.query(PredictionLog).filter_by(**filters).order_by(PredictionLog.id).all()
    finally:
        db.close()


def predict(w, **extra):
    r = w.client.post("/predict", json={"features": w.features, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def label(w, log_id, value=200_000.0):
    assert w.client.post("/label", json={"log_id": log_id, "true_label": value}).status_code == 200


def set_shadow(w, version):
    assert w.client.post("/admin/set_shadow", headers=H, json={"version": version}).status_code == 200


def paired_rows(n, prod_err=20_000.0, shadow_err=lambda i: 5_000.0 + 100.0 * i,
                prod_latency=lambda i: 5.0, shadow_latency=lambda i: 6.0):
    """n labelled production(v1)/shadow(v2) pairs sharing a request_id, with NO timestep at all."""
    for i in range(1, n + 1):
        true = 300_000.0
        for version, is_shadow, err, lat in (("v1", False, prod_err, prod_latency(i)), ("v2", True, shadow_err(i), shadow_latency(i))):
            add_log(request_id=f"req-{i}", model_version=version, is_shadow=is_shadow,
                    prediction=true + err, true_label=true, latency_ms=lat)


def stats(w):
    return w.client.get("/admin/stats", headers=H).json()


def decide(w, request_id, decision="approve"):
    return w.client.post("/admin/promote", headers=H, json={"promotion_request_id": request_id, "decision": decision})


# =================================================================== BUG-3: request-id pairing
def test_predict_returns_a_request_id_shared_by_the_production_and_shadow_rows(world):
    set_shadow(world, "v2")
    body = predict(world)
    assert body["request_id"]
    rows = logs(request_id=body["request_id"])
    assert sorted((r.model_version, r.is_shadow) for r in rows) == [("v1", False), ("v2", True)]
    assert len({r.request_id for r in logs()}) == 1 and body["log_id"] in {r.id for r in rows}


def test_request_ids_are_unique_per_request(world):
    ids = {predict(world)["request_id"] for _ in range(5)}
    assert len(ids) == 5


def test_traffic_without_timesteps_gets_paired_labelled_and_evaluated(world):
    from evaluation import evaluation_gate
    set_shadow(world, "v2")
    for _ in range(35):
        label(world, predict(world)["log_id"])          # no timestep anywhere
    assert all(r.timestep is None for r in logs())
    prod, shadow, ids, *_ = evaluation_gate.load_paired_data("v1", "v2")
    assert len(ids) == 35 and len(prod) == len(shadow) == 35
    result = evaluation_gate.evaluate(30, status=world.app.status())
    assert result["outcome"] != "waiting" and result["n"] == 35


def test_labelling_a_request_labels_its_shadow_twin_and_only_that_one(world):
    set_shadow(world, "v2")
    first = predict(world, timestep=5)
    set_shadow(world, "v3")                               # a different shadow, SAME timestep
    second = predict(world, timestep=5)
    label(world, first["log_id"], 123_456.0)

    by_req = {(r.request_id, r.model_version): r.true_label for r in logs(is_shadow=True)}
    assert by_req[(first["request_id"], "v2")] == 123_456.0
    assert by_req[(second["request_id"], "v3")] is None   # old code matched on timestep and would have labelled this


def test_two_successive_shadows_with_overlapping_timesteps_never_cross_contaminate(world):
    from evaluation import evaluation_gate
    set_shadow(world, "v2")
    for t in range(1, 6):
        label(world, predict(world, timestep=t)["log_id"], 111_111.0)
    set_shadow(world, "v3")
    for t in range(1, 6):                                 # same timesteps again, now with v3 shadowing
        label(world, predict(world, timestep=t)["log_id"], 222_222.0)
    for version, truth in (("v2", 111_111.0), ("v3", 222_222.0)):
        labels = {r.true_label for r in logs(is_shadow=True, model_version=version)}
        assert labels == {truth}, f"{version} shadow rows carry another request's label: {labels}"
    _, _, ids, *_ = evaluation_gate.load_paired_data("v1", "v3")
    assert len(ids) == 5


def test_a_legacy_row_without_a_request_id_is_labelled_without_error(world):
    legacy = add_log(timestep=7)                          # as written before request ids existed
    label(world, legacy, 99.0)
    assert logs(id=legacy)[0].true_label == 99.0


def test_the_gate_copes_with_a_challenger_identical_to_production(world):
    from evaluation import evaluation_gate
    set_shadow(world, "v2")                               # v2 is a byte-copy of v1 in this fixture
    for _ in range(32):
        label(world, predict(world)["log_id"])
    result = evaluation_gate.evaluate(30, status=world.app.status())
    assert result["outcome"] == "not_passed"              # scipy's Wilcoxon raises when every difference is zero


def test_the_drift_monitor_survives_traffic_without_timesteps(world, reference_dir):
    from monitor import drift_monitor
    from serving.db import SessionLocal, DriftEvent
    for _, row in STREAM.head(200).iterrows():
        add_log(features={c: float(row[c]) for c in drift_monitor.MONITORED_COLUMNS})
    first = drift_monitor.run_once(200)
    assert first is not None
    db = SessionLocal()
    try:
        event = db.query(DriftEvent).order_by(DriftEvent.id.desc()).first()
        assert event.window_start_timestep is None and event.window_end_timestep is None
        assert event.window_end_log_id == logs()[-1].id
    finally:
        db.close()
    assert drift_monitor.run_once(200) is None            # nothing new arrived: skipped by row id, not by timestep


def test_view_logs_survives_missing_timesteps(world, capsys):
    from scripts import view_logs
    add_log(request_id="r1")
    view_logs.cmd_logs(5)
    view_logs.cmd_summary()
    assert "r1" not in capsys.readouterr().err


# =================================================================== BUG-2: per-version drift reference
@pytest.fixture
def reference_dir(world, monkeypatch):
    from monitor import reference
    monkeypatch.setattr(reference, "MODELS_DIR", world.models)
    return world.models


def _drifted_window(n=200):
    drifted = STREAM[STREAM["drifted"] == 1]
    return drifted.head(n), drifted.iloc[n:]


def _log_window(df):
    from monitor import drift_monitor
    for _, row in df.iterrows():
        add_log(features={c: float(row[c]) for c in drift_monitor.MONITORED_COLUMNS})


def test_a_reference_snapshot_holds_the_monitored_features_and_the_target(tmp_path):
    from monitor import reference, drift_monitor
    path = reference.save_reference(STREAM.head(50), "v7", tmp_path)
    assert path.name == "reference_v7.parquet"
    df = reference.load_reference_for("v7", tmp_path)
    assert list(df.columns) == drift_monitor.MONITORED_COLUMNS + ["SalePrice"]
    assert len(df) == 50 and reference.load_reference_for("v8", tmp_path) is None


def test_training_a_baseline_saves_its_reference(tmp_path, monkeypatch):
    import models.train_baseline as tb
    from monitor import reference
    monkeypatch.setattr(tb, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(tb, "FINAL_GB_PARAMS", {**tb.FINAL_GB_PARAMS, "n_estimators": 5})
    tb.train("v1")
    df = reference.load_reference_for("v1", tmp_path)
    assert df is not None and len(df) == 700 and "SalePrice" in df.columns   # the 80% split the model was fitted on


def test_training_a_challenger_saves_the_reference_of_its_own_training_rows(tmp_path, monkeypatch):
    from retrain import retrain_pipeline as rp
    from monitor import reference
    monkeypatch.setattr(rp, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rp, "FINAL_GB_PARAMS", {**rp.FINAL_GB_PARAMS, "n_estimators": 5})
    rp.train_challenger(STREAM.iloc[233:533].drop(columns=["timestep", "drifted"]).reset_index(drop=True), "v9")
    df = reference.load_reference_for("v9", tmp_path)
    assert len(df) == 240                                  # 80% of 300 rows
    assert df["OverallQual"].mean() > STREAM.iloc[:200]["OverallQual"].mean()   # it is the DRIFTED regime, not the baseline's


def test_drift_is_measured_against_the_current_production_models_data(world, reference_dir):
    from monitor import drift_monitor
    window, _ = _drifted_window()
    _log_window(window)
    first = drift_monitor.run_once(200)
    assert first["triggered"], "the drifted regime must look drifted against v1's training data"


def test_after_promoting_a_challenger_trained_on_the_new_regime_the_same_window_stops_triggering(world, reference_dir):
    from monitor import drift_monitor, reference
    from serving.db import SessionLocal, DriftEvent
    window, new_regime = _drifted_window()
    _log_window(window)
    assert drift_monitor.run_once(200)["triggered"]       # vs v1

    reference.save_reference(new_regime, "v2", reference_dir)    # what a retrain on the new regime would have saved
    set_shadow(world, "v2")
    assert decide(world, add_promotion_request("v2")).status_code == 200

    again = drift_monitor.run_once(200)                    # the SAME rows, no new traffic: must be re-evaluated, not skipped
    assert again is not None and not again["triggered"]
    db = SessionLocal()
    try:
        used = [e.reference_version for e in db.query(DriftEvent).order_by(DriftEvent.id)]
    finally:
        db.close()
    assert used == ["v1", "v2"]


def test_the_v1_reference_is_created_from_the_training_data_when_missing(world, reference_dir):
    from monitor import drift_monitor, reference
    assert reference.load_reference_for("v1", reference_dir) is None
    window, _ = _drifted_window()
    _log_window(window)
    drift_monitor.run_once(200)
    assert reference.load_reference_for("v1", reference_dir) is not None


def test_a_production_model_without_a_snapshot_falls_back_to_the_baseline_and_says_so(world, reference_dir):
    from monitor import drift_monitor
    from serving.db import SessionLocal, DriftEvent
    set_shadow(world, "v2")
    decide(world, add_promotion_request("v2"))             # v2 is production but trained before snapshots existed
    window, _ = _drifted_window()
    _log_window(window)
    drift_monitor.run_once(200)
    db = SessionLocal()
    try:
        assert db.query(DriftEvent).order_by(DriftEvent.id.desc()).first().reference_version == "v1"
    finally:
        db.close()


def test_the_registry_records_where_a_versions_reference_lives(world, reference_dir):
    from monitor import reference
    from serving.db import SessionLocal, ModelRegistry
    reference.save_reference(STREAM.head(30), "v2", reference_dir)
    set_shadow(world, "v2")
    db = SessionLocal()
    try:
        assert db.get(ModelRegistry, "v2").reference_path == "reference_v2.parquet"
    finally:
        db.close()


def test_retraining_survives_a_neighbourhood_with_no_known_lot_frontage():
    """A small retraining sample can contain a neighbourhood whose LotFrontage is missing for EVERY row;
    its median is NaN, which used to leak through and crash `astype(int)` (so the whole retrain failed)."""
    from data.house_price_preprocessor import HousePricePreprocessor
    df = pd.read_csv(ROOT / "data" / "training_data.csv").head(150).copy()
    df.loc[df["Neighborhood"] == df["Neighborhood"].iloc[0], "LotFrontage"] = np.nan
    pre = HousePricePreprocessor().fit(df)
    out = pre.transform(df)
    assert not out.isna().any().any() and len(out) == 150


# =================================================================== BUG-8: latency
def test_the_first_requests_of_a_freshly_loaded_model_are_warm_up_and_record_no_latency(world, monkeypatch):
    monkeypatch.setattr(world.app, "WARMUP_CALLS", 3)
    ids = [predict(world)["log_id"] for _ in range(5)]
    latencies = [logs(id=i)[0].latency_ms for i in ids]
    assert latencies[:3] == [None, None, None] and all(v is not None and v > 0 for v in latencies[3:])


def test_shadow_latency_does_not_include_loading_the_model_from_disk(world, monkeypatch):
    monkeypatch.setattr(world.app, "WARMUP_CALLS", 0)
    real = world.app.load_model

    def slow_first_load(version):
        if version == "v2" and "v2" not in world.app.STATE["models"]:
            time.sleep(0.6)                                # a cold disk read
        return real(version)
    monkeypatch.setattr(world.app, "load_model", slow_first_load)
    set_shadow(world, "v2")
    world.app.STATE["models"].pop("v2")                    # force the lazy load to happen inside /predict
    predict(world)
    shadow = logs(is_shadow=True)[0]
    assert shadow.latency_ms < 300, f"shadow latency {shadow.latency_ms} ms still contains the model load"


def test_the_gate_reports_p50_and_p95_inference_latency(world):
    from evaluation import evaluation_gate
    set_shadow(world, "v2")
    paired_rows(100, prod_latency=lambda i: float(i), shadow_latency=lambda i: 2.0 * i)
    evaluation_gate.evaluate(30, status=world.app.status())
    row = next(r for r in world.client.get("/admin/promotion_requests", headers=H).json())
    assert row["production_latency_ms"] == pytest.approx(50.5)           # p50 (the median), not the mean-and-outliers
    assert row["production_latency_p95_ms"] == pytest.approx(95.05)
    assert row["challenger_latency_ms"] == pytest.approx(101.0)
    assert row["challenger_latency_p95_ms"] == pytest.approx(190.1)


def test_one_slow_outlier_does_not_move_the_reported_latency_much(world):
    from evaluation import evaluation_gate
    set_shadow(world, "v2")
    paired_rows(60, prod_latency=lambda i: 1000.0 if i == 1 else 5.0)    # one cold request
    evaluation_gate.evaluate(30, status=world.app.status())
    row = world.client.get("/admin/promotion_requests", headers=H).json()[0]
    assert row["production_latency_ms"] == pytest.approx(5.0)


def test_stats_report_p50_and_p95_for_the_recent_requests(world):
    for i in range(1, 101):
        add_log(latency_ms=float(i))
    s = stats(world)
    assert s["production_latency_ms"] == pytest.approx(50.5, abs=0.1)
    assert s["production_latency_p95_ms"] == pytest.approx(95.05, abs=0.1)


def test_warm_up_rows_are_ignored_by_the_latency_numbers(world):
    add_log(latency_ms=None)
    add_log(latency_ms=10.0)
    assert stats(world)["production_latency_ms"] == 10.0


# =================================================================== BUG-9: stats per version, thresholds from config
def _labelled(version, n, err):
    for _ in range(n):
        add_log(model_version=version, prediction=300_000.0 + err, true_label=300_000.0, latency_ms=5.0)


def test_mae_is_computed_for_the_current_production_version_only(world):
    _labelled("v1", 20, 20_000.0)
    assert stats(world)["production_mae"] == pytest.approx(20_000.0)
    set_shadow(world, "v2")
    decide(world, add_promotion_request("v2"))
    _labelled("v2", 10, 5_000.0)
    s = stats(world)
    assert s["production_version"] == "v2"
    assert s["production_mae"] == pytest.approx(5_000.0)    # not the 15k blend of both versions
    assert s["production_mae_n"] == 10


def test_a_just_promoted_model_with_no_labels_yet_has_no_mae_instead_of_its_predecessors(world):
    _labelled("v1", 20, 20_000.0)
    set_shadow(world, "v2")
    decide(world, add_promotion_request("v2"))
    s = stats(world)
    assert s["production_mae"] is None and s["production_mae_n"] == 0


def test_stats_carry_the_models_own_validation_error_and_the_thresholds(world):
    s = stats(world)
    assert s["expected_mae"] == pytest.approx(19958.36, abs=0.01)       # model_v1.meta.json "mae"
    assert (s["mae_warn_ratio"], s["mae_alert_ratio"]) == (1.3, 2.0)


def test_the_thresholds_are_configurable(world, monkeypatch):
    monkeypatch.setenv("MAE_WARN_RATIO", "1.1")
    monkeypatch.setenv("MAE_ALERT_RATIO", "1.5")
    s = stats(world)
    assert (s["mae_warn_ratio"], s["mae_alert_ratio"]) == (1.1, 1.5)
    monkeypatch.setenv("MAE_WARN_RATIO", "banana")
    assert stats(world)["mae_warn_ratio"] == 1.3                       # a bad value falls back to the default


def test_a_model_without_metadata_has_no_expected_mae(world):
    (world.models / "model_v1.meta.json").unlink()
    assert stats(world)["expected_mae"] is None


# =================================================================== the migration these need
OLD_SCHEMA = [
    "CREATE TABLE prediction_logs (id INTEGER PRIMARY KEY, timestep INTEGER, features JSON NOT NULL, prediction FLOAT NOT NULL,"
    " model_version VARCHAR NOT NULL, is_shadow BOOLEAN, true_label FLOAT, served_at FLOAT)",
    "CREATE TABLE drift_events (id INTEGER PRIMARY KEY, checked_at FLOAT, window_start_timestep INTEGER, window_end_timestep INTEGER,"
    " drift_score FLOAT NOT NULL, method VARCHAR, threshold FLOAT NOT NULL, triggered BOOLEAN, details JSON)",
    "CREATE TABLE promotion_requests (id INTEGER PRIMARY KEY, challenger_version VARCHAR NOT NULL,"
    " production_version_at_request VARCHAR NOT NULL, created_at FLOAT, challenger_mae FLOAT, production_mae FLOAT,"
    " n_labels_compared INTEGER, status VARCHAR, decided_at FLOAT, decided_by VARCHAR)",
    "INSERT INTO prediction_logs (id, timestep, features, prediction, model_version, is_shadow, served_at) VALUES (1, 4, '{}', 10.0, 'v1', 0, 1.0)",
]


def _columns(engine, table):
    from sqlalchemy import inspect
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_migrate_adds_the_new_columns_to_an_old_database_and_keeps_its_rows(tmp_path):
    from sqlalchemy import create_engine, text
    from serving import db as dbmod
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        for stmt in OLD_SCHEMA:
            conn.execute(text(stmt))

    dbmod.migrate(engine)

    assert {"request_id", "latency_ms"} <= _columns(engine, "prediction_logs")
    assert {"explanation", "window_start_log_id", "window_end_log_id", "reference_version"} <= _columns(engine, "drift_events")
    assert {"explanation", "challenger_latency_ms", "production_latency_ms",
            "challenger_latency_p95_ms", "production_latency_p95_ms"} <= _columns(engine, "promotion_requests")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT timestep, request_id FROM prediction_logs WHERE id = 1")).one() == (4, None)
        names = {r[1] for r in conn.execute(text("PRAGMA index_list(prediction_logs)"))}
    assert "ix_prediction_logs_request_id" in names


def test_migrate_is_idempotent(tmp_path):
    from sqlalchemy import create_engine, text
    from serving import db as dbmod
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        for stmt in OLD_SCHEMA:
            conn.execute(text(stmt))
    dbmod.migrate(engine)
    dbmod.migrate(engine)                                   # nothing to do the second time, and no error
    assert "request_id" in _columns(engine, "prediction_logs")
