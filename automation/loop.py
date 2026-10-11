"""
Automation loop. This is the piece that actually makes the pipeline
"self-healing" instead of "self-healing, if you remember to run three
scripts in the right order." On a schedule, it:

  1. Runs the drift monitor.
  2. If drift is detected AND no shadow challenger is already deployed,
     triggers a retrain (a challenger already in shadow means one is still
     being evaluated — don't start a second one on top of it).
  3. If a shadow challenger IS deployed (whether just started or from an
     earlier cycle), runs the evaluation gate to see if there's now enough
     evidence to decide.

What this loop will NEVER do: call /admin/promote. That endpoint is only
ever called by a human, manually, after reviewing a promotion request.
This loop can get a challenger all the way to "awaiting your decision" —
never further.

Run:
    python automation/loop.py
Configure via environment variables (all optional, shown with defaults):
    CHECK_INTERVAL_SECONDS=60
    DRIFT_WINDOW=200
    RETRAIN_N_RECENT=500
    GATE_MIN_SAMPLES=30
    SERVING_API_URL=http://localhost:8000   (used by retrain_pipeline / evaluation_gate)
"""
import os
import time

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

from monitor.drift_monitor import run_once as check_drift
from retrain.retrain_pipeline import run_retrain
from evaluation.evaluation_gate import run_gate

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
DRIFT_WINDOW = int(os.environ.get("DRIFT_WINDOW", "200"))
RETRAIN_N_RECENT = int(os.environ.get("RETRAIN_N_RECENT", "500"))
GATE_MIN_SAMPLES = int(os.environ.get("GATE_MIN_SAMPLES", "30"))
API_URL = os.environ.get("SERVING_API_URL", "http://localhost:8000")


def get_status():
    try:
        return requests.get(f"{API_URL}/status", timeout=5).json()
    except requests.exceptions.RequestException as e:
        print(f"Could not reach serving API at {API_URL}: {e}")
        return None


def cycle():
    print(f"\n=== automation cycle: {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    status = get_status()
    if status is None:
        print("Skipping this cycle — serving API unreachable.")
        return

    shadow_active = bool(status.get("shadow_version"))

    drift_result = check_drift(DRIFT_WINDOW)

    if drift_result and drift_result["triggered"] and not shadow_active:
        print("Drift detected and no challenger currently in shadow — triggering retrain.")
        new_version = run_retrain(RETRAIN_N_RECENT)
        shadow_active = new_version is not None
    elif drift_result and drift_result["triggered"] and shadow_active:
        print("Drift detected, but a challenger is already in shadow "
              f"({status['shadow_version']}) — waiting for the evaluation gate "
              "to resolve it before starting another retrain.")

    if shadow_active:
        run_gate(GATE_MIN_SAMPLES)
    else:
        print("No shadow challenger active — nothing for the evaluation gate to check yet.")

    print("=== cycle complete ===")


def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(cycle, "interval", seconds=CHECK_INTERVAL)
    print(f"Automation loop starting — checking every {CHECK_INTERVAL}s "
          f"(drift window={DRIFT_WINDOW}, retrain n_recent={RETRAIN_N_RECENT}, "
          f"gate min_samples={GATE_MIN_SAMPLES})")
    cycle()  # run once immediately instead of waiting for the first interval
    scheduler.start()


if __name__ == "__main__":
    main()
