"""
Replays stream_data.csv row-by-row against the serving API as if it were
live traffic, simulating the delayed-label problem: predictions are sent
immediately, but the true label for a row is only submitted after a
configurable delay (LABEL_DELAY rows later) — mimicking, e.g., a delivery
time that's only known once the delivery actually completes.

Usage:
    python simulator/stream_simulator.py --speed 0 --limit 500
    (speed=0 means no sleep between rows -> runs as fast as possible;
     set e.g. --speed 0.05 for a slower, watchable demo)
"""
import argparse
import time
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "stream_data.csv"
API_URL = "http://localhost:8000"
LABEL_DELAY = 20  # rows of "lag" before the true label becomes available
REQUEST_TIMEOUT = 15  # seconds — generous enough to tolerate transient slowness
MAX_RETRIES = 3

NON_FEATURE_COLS = {"SalePrice", "timestep", "drifted"}

session = requests.Session()  # reuses one TCP connection instead of opening a new one per request


def to_jsonable(value):
    """Converts pandas/numpy scalar types to plain JSON-safe Python types,
    and NaN to None (JSON null) since the preprocessor treats both as
    'missing' the same way."""
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def post_with_retry(path, payload):
    """
    Posts to the API, retrying on transient network errors (timeouts,
    connection resets) with a short backoff — real clients hitting a real
    API over a real network should tolerate one hiccup, not crash on it.
    Only network-level failures are retried; a real error response from
    the API (4xx/5xx) is NOT retried, since retrying won't fix a bad
    request or a genuine server error.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(f"{API_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = 0.5 * attempt
                print(f"  [retry {attempt}/{MAX_RETRIES}] {path} failed ({e.__class__.__name__}), "
                      f"retrying in {wait}s...")
                time.sleep(wait)
    raise last_error


def run(speed: float, limit: int | None):
    df = pd.read_csv(DATA_PATH)
    if limit:
        df = df.iloc[:limit]

    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    has_drift_col = "drifted" in df.columns  # optional: only the generated demo stream has it
    pending_labels = deque()  # (row_index_to_release_at, log_id, true_label)

    n_drift_rows_seen = 0

    for i, row in df.iterrows():
        features = {c: to_jsonable(row[c]) for c in feature_cols}
        timestep = int(row["timestep"])

        resp = post_with_retry("/predict", {"features": features, "timestep": timestep})
        log_id = resp.json()["log_id"]

        pending_labels.append((i + LABEL_DELAY, log_id, float(row["SalePrice"])))

        # release any labels whose delay has elapsed
        while pending_labels and pending_labels[0][0] <= i:
            _, due_log_id, true_label = pending_labels.popleft()
            post_with_retry("/label", {"log_id": due_log_id, "true_label": true_label})

        row_drifted = bool(row["drifted"]) if has_drift_col else False
        if row_drifted:
            n_drift_rows_seen += 1

        if i % 100 == 0:
            print(f"[t={timestep}] sent prediction (log_id={log_id}), "
                  f"drifted={row_drifted}")

        if speed > 0:
            time.sleep(speed)

    # flush remaining pending labels
    for _, due_log_id, true_label in pending_labels:
        post_with_retry("/label", {"log_id": due_log_id, "true_label": true_label})

    print(f"Done. Streamed {len(df)} rows, {n_drift_rows_seen} were post-drift.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=float, default=0.0,
                         help="Seconds to sleep between rows (0 = as fast as possible)")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only stream the first N rows (for quick tests)")
    args = parser.parse_args()
    run(args.speed, args.limit)
