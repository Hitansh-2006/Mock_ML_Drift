"""
Drift monitor. Responsibilities:
  1. Load the reference distribution (the training data the current
     production model was trained on).
  2. Pull the most recent N predictions from prediction_logs (the live
     traffic the serving API already logged for us).
  3. Run a KS-test per feature: reference distribution vs. recent window.
  4. Combine the per-feature results into one drift decision.
  5. Write the result to drift_events, whether or not it triggers — so you
     can see the drift score trend over time, not just the moments it fires.

This does NOT retrain anything itself. It only decides "has drift happened"
and records that decision. Step 3 (retrain pipeline) will watch this table.

Run once:
    python monitor/drift_monitor.py --window 200

Run on a schedule (checks every 30s):
    python monitor/drift_monitor.py --window 200 --loop --interval 30
"""
import argparse
import json
import time
from pathlib import Path

import pandas as pd
from scipy.stats import ks_2samp
from sqlalchemy import select

from monitor import reference
from monitor.reference import MONITORED_COLUMNS  # the curated raw columns; see monitor/reference.py for why
from serving import registry
from serving.db import SessionLocal, PredictionLog, DriftEvent, init_db
from llm.explain import explain_drift

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRAINING_DATA_PATH = DATA_DIR / "training_data.csv"

# A feature triggers "drifted" if its KS statistic exceeds this AND the
# difference is statistically significant (p < 0.05, i.e. very unlikely to
# be random noise). Overall drift triggers if enough features drift.
KS_STAT_THRESHOLD = 0.15
P_VALUE_THRESHOLD = 0.05
MIN_DRIFTED_FEATURES_TO_TRIGGER = 2  # out of however many monitored columns exist


def current_production_version():
    """Which model is in production RIGHT NOW, according to the DB registry (v1 before any exists)."""
    db = SessionLocal()
    try:
        return registry.current_roles(db)[0] or reference.BASELINE_VERSION
    finally:
        db.close()


def resolve_reference(version):
    """
    The training-data snapshot to compare live traffic with: the one saved by `version`.
    A version trained before snapshots existed has none; it then falls back to the
    baseline's (made on demand from training_data.csv). Returns (df, feature_cols, version_used)
    so the drift event can record whose data it really was compared against.
    """
    df = reference.load_reference_for(version)
    used = version
    if df is None:
        df = reference.ensure_baseline_reference()
        used = reference.BASELINE_VERSION
        if version != used:
            print(f"No reference snapshot for {version} (trained before snapshots existed); "
                  f"comparing with the baseline's training data instead.")
    feature_cols = [c for c in MONITORED_COLUMNS if c in df.columns]
    return df[feature_cols].dropna(), feature_cols, used


def load_reference():
    """Back-compat helper: the current production model's reference as (df, feature_cols)."""
    df, feature_cols, _ = resolve_reference(current_production_version())
    return df, feature_cols


def load_recent_window(feature_cols, window_size):
    """
    Pulls the most recent `window_size` non-shadow predictions from the
    database, ordered by id (insertion order = arrival order), and returns
    them as a DataFrame plus the timestep range they cover.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(PredictionLog)
            .filter(PredictionLog.is_shadow == False)  # noqa: E712
            .order_by(PredictionLog.id.desc())
            .limit(window_size)
            .all()
        )
    finally:
        db.close()

    if not rows:
        return None, None, None, None, None

    rows = list(reversed(rows))  # back to chronological order
    records = [r.features for r in rows]
    recent_df = pd.DataFrame(records)[feature_cols].apply(pd.to_numeric, errors="coerce").dropna()
    # timestep is optional metadata (real traffic has none), so the window is identified by row ids
    timesteps = [r.timestep for r in rows if r.timestep is not None]
    ts_range = (min(timesteps), max(timesteps)) if timesteps else (None, None)
    return recent_df, ts_range[0], ts_range[1], rows[0].id, rows[-1].id


def compute_drift_scores(reference_df, recent_df, feature_cols):
    """
    Runs a KS-test per feature. Returns a dict of per-feature results plus
    an overall summary.
    """
    per_feature = {}
    drifted_count = 0

    for col in feature_cols:
        stat, p_value = ks_2samp(reference_df[col], recent_df[col])
        is_drifted = bool((stat >= KS_STAT_THRESHOLD) and (p_value < P_VALUE_THRESHOLD))
        if is_drifted:
            drifted_count += 1
        per_feature[col] = {
            "ks_statistic": round(float(stat), 4),
            "p_value": round(float(p_value), 6),
            "drifted": is_drifted,
        }

    triggered = drifted_count >= MIN_DRIFTED_FEATURES_TO_TRIGGER
    overall_score = max(v["ks_statistic"] for v in per_feature.values())

    return {
        "per_feature": per_feature,
        "n_drifted_features": drifted_count,
        "overall_score": round(overall_score, 4),
        "triggered": triggered,
    }


def run_once(window_size):
    init_db()
    production_version = current_production_version()
    reference_df, feature_cols, reference_version = resolve_reference(production_version)
    recent_df, ts_start, ts_end, start_id, end_id = load_recent_window(feature_cols, window_size)

    if recent_df is None or len(recent_df) < window_size:
        n = 0 if recent_df is None else len(recent_df)
        print(f"Not enough logged predictions yet ({n}/{window_size}). Skipping check.")
        return None

    # If the most recent window ends at the same logged request as the last check AND was
    # compared with the same reference, nothing new has arrived: re-running the same KS
    # test on the same rows would only repeat the result. (Row ids, not timesteps: real
    # traffic has no timesteps.) A different reference is NOT a repeat: right after a
    # promotion the same rows must be judged against the new model's training data.
    db = SessionLocal()
    try:
        last_event = db.query(DriftEvent).order_by(DriftEvent.id.desc()).first()
    finally:
        db.close()

    if last_event and last_event.window_end_log_id == end_id and last_event.reference_version == reference_version:
        print(f"No new predictions since the last check (still ending at request #{end_id}, "
              f"compared with {reference_version}). Skipping — nothing new to evaluate.")
        return None

    result = compute_drift_scores(reference_df, recent_df, feature_cols)

    # Only bother generating an explanation when there's actually something
    # to explain — a "no drift" result needs no plain-English summary.
    explanation = None
    if result["triggered"]:
        explanation = explain_drift(result["per_feature"], result["n_drifted_features"], result["overall_score"])

    db = SessionLocal()
    try:
        event = DriftEvent(
            window_start_timestep=ts_start,
            window_end_timestep=ts_end,
            drift_score=result["overall_score"],
            method="ks_test",
            threshold=KS_STAT_THRESHOLD,
            triggered=result["triggered"],
            details=result["per_feature"],
            explanation=explanation,
            window_start_log_id=start_id,
            window_end_log_id=end_id,
            reference_version=reference_version,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
    finally:
        db.close()

    status = "DRIFT DETECTED" if result["triggered"] else "no drift"
    window = f"requests #{start_id}-#{end_id}" + (f", timesteps {ts_start}-{ts_end}" if ts_end is not None else "")
    print(
        f"[{window} vs {reference_version}] {status} | "
        f"score={result['overall_score']} | "
        f"{result['n_drifted_features']}/{len(feature_cols)} features flagged | "
        f"event_id={event.id}"
    )
    for col, v in result["per_feature"].items():
        flag = "  <-- DRIFTED" if v["drifted"] else ""
        print(f"    {col}: ks={v['ks_statistic']} p={v['p_value']}{flag}")

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=200,
                         help="Number of most recent predictions to check")
    parser.add_argument("--loop", action="store_true",
                         help="Keep checking on an interval instead of running once")
    parser.add_argument("--interval", type=int, default=30,
                         help="Seconds between checks when --loop is set")
    args = parser.parse_args()

    if args.loop:
        print(f"Monitoring every {args.interval}s (window={args.window})...")
        while True:
            run_once(args.window)
            time.sleep(args.interval)
    else:
        run_once(args.window)


if __name__ == "__main__":
    main()
