"""
Automation loop. This is the piece that actually makes the pipeline
"self-healing" instead of "self-healing, if you remember to run three
scripts in the right order." Each cycle:

  1. Runs the drift monitor.
  2. If drift is detected AND no shadow challenger is already deployed,
     triggers a retrain (a challenger already in shadow means one is still
     being evaluated — don't start a second one on top of it).
  3. If a shadow challenger IS deployed (whether just started or from an
     earlier cycle), runs the evaluation gate to see if there's now enough
     evidence to decide.

What this loop will NEVER do: change which model serves production. It can
get a challenger all the way to "awaiting your decision" — never further.
That decision is made by a human in the dashboard.

There is exactly ONE implementation of a cycle: `cycle()`. It is used by

  - the dashboard's Automation button (automation/controller.py runs it on an
    APScheduler job inside the serving process, calling the serving code
    directly), and
  - this module's CLI, for headless use (`python -m automation.loop`), which
    talks to a running serving API over HTTP.

Run headless:
    python -m automation.loop
Configure via environment variables (all optional, shown with defaults):
    CHECK_INTERVAL_SECONDS=60
    DRIFT_WINDOW=200
    RETRAIN_N_RECENT=500
    GATE_MIN_SAMPLES=30
    SERVING_API_URL=http://localhost:8000   (CLI only)
"""
import os
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

from monitor.drift_monitor import run_once as check_drift
from retrain.retrain_pipeline import run_retrain
from evaluation.evaluation_gate import evaluate as run_gate

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
DRIFT_WINDOW = int(os.environ.get("DRIFT_WINDOW", "200"))
RETRAIN_N_RECENT = int(os.environ.get("RETRAIN_N_RECENT", "500"))
GATE_MIN_SAMPLES = int(os.environ.get("GATE_MIN_SAMPLES", "30"))
API_URL = os.environ.get("SERVING_API_URL", "http://localhost:8000")


@dataclass
class CycleDeps:
    """How a cycle reaches the serving side, so the same code runs in-process or over HTTP."""
    get_status: Callable[[], Optional[dict]]   # -> {"production_version", "shadow_version", ...} or None if unreachable
    deploy_shadow: Callable[[str], dict]       # puts a freshly trained version into the shadow slot
    retrain_lock: threading.Lock               # shared with the dashboard's Retrain button


def get_status():
    try:
        return requests.get(f"{API_URL}/status", timeout=5).json()
    except requests.exceptions.RequestException as e:
        print(f"Could not reach serving API at {API_URL}: {e}")
        return None


def http_deps() -> CycleDeps:
    """Deps for the headless CLI: a separate process, so it goes through the serving API."""
    from retrain.retrain_pipeline import deploy_shadow
    return CycleDeps(get_status=get_status, deploy_shadow=deploy_shadow, retrain_lock=threading.Lock())


def _new_result() -> dict:
    return {
        "drift_checked": False,
        "drift_triggered": False,
        "action": "none",          # none | retrain | gate
        "gate_result": None,
        "error": None,
        "message": "",
        "retrain_skipped": False,
        "new_version": None,
    }


def _gate_message(gate: dict) -> str:
    outcome, n, need = gate["outcome"], gate.get("n"), gate.get("min_samples")
    pid = gate.get("promotion_request_id")
    return {
        "waiting": f"Waiting for data: {n}/{need} paired labels",
        "request_created": f"Gate passed → promotion request #{pid} awaits your approval",
        "request_updated": f"Promotion request #{pid} updated with {n} samples — awaiting your approval",
        "request_withdrawn": f"Promotion request #{pid} withdrawn: it no longer passes the gate",
        "no_new_data": f"Promotion request #{pid} awaits your approval (no new paired data since)",
        "not_passed": f"Challenger {gate.get('challenger_version')} is not clearly better yet ({gate.get('reason', '')})",
        "no_shadow": "No challenger in shadow",
    }.get(outcome, outcome)


def _summarize(r: dict) -> str:
    if r["error"]:
        return f"Error: {r['error']}"
    if r["retrain_skipped"]:
        return "Drift detected, but a retrain is already running — skipped"
    gate = r["gate_result"]
    if r["action"] == "retrain":
        text = f"Drift detected → trained {r['new_version']}"
        return text + (f"; {_gate_message(gate)}" if gate and gate["outcome"] != "waiting" else "; challenger now in shadow")
    if gate:
        prefix = "Drift detected; " if r["drift_triggered"] else ""
        return prefix + _gate_message(gate)
    if not r["drift_checked"]:
        return "Waiting for traffic: not enough new predictions to check drift"
    if r["drift_triggered"]:
        return "Drift detected, but there is not enough labelled data to retrain yet"
    return "No drift"


def _retrain_if_free(deps: CycleDeps, result: dict) -> None:
    """Retrain under the lock shared with the Retrain button; skip (and say so) if it's taken."""
    if not deps.retrain_lock.acquire(blocking=False):
        result["retrain_skipped"] = True
        print("Drift detected, but a retrain is already running — skipping this cycle's retrain.")
        return
    try:
        print("Drift detected and no challenger currently in shadow — triggering retrain.")
        new_version = run_retrain(RETRAIN_N_RECENT, deploy=deps.deploy_shadow)
    finally:
        deps.retrain_lock.release()
    if new_version:
        result["action"] = "retrain"
        result["new_version"] = new_version


def _run(deps: CycleDeps, result: dict) -> None:
    status = deps.get_status()
    if status is None:
        result["error"] = "serving API unreachable"
        print("Skipping this cycle — serving API unreachable.")
        return

    shadow_active = bool(status.get("shadow_version"))
    drift = check_drift(DRIFT_WINDOW)
    result["drift_checked"] = drift is not None
    result["drift_triggered"] = bool(drift and drift["triggered"])

    if result["drift_triggered"] and not shadow_active:
        _retrain_if_free(deps, result)
        if result["action"] == "retrain":
            status = deps.get_status() or status
            shadow_active = bool(status.get("shadow_version"))
    elif result["drift_triggered"]:
        print("Drift detected, but a challenger is already in shadow "
              f"({status['shadow_version']}) — waiting for the evaluation gate "
              "to resolve it before starting another retrain.")

    if shadow_active:
        result["gate_result"] = run_gate(GATE_MIN_SAMPLES, status=status)
        if result["action"] == "none":
            result["action"] = "gate"
    else:
        print("No shadow challenger active — nothing for the evaluation gate to check yet.")


def cycle(deps: Optional[CycleDeps] = None) -> dict:
    """
    One drift → retrain → gate pass. Never raises: a failure is returned in
    result["error"] so a scheduler (or the CLI loop) just carries on.
    """
    deps = deps or http_deps()
    print(f"\n=== automation cycle: {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    result = _new_result()
    try:
        _run(deps, result)
    except Exception as e:
        traceback.print_exc()
        result["error"] = f"{type(e).__name__}: {e}"
    result["message"] = _summarize(result)
    print(f"=== cycle complete: {result['message']} ===")
    return result


def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(cycle, "interval", seconds=CHECK_INTERVAL, max_instances=1, coalesce=True)
    print(f"Automation loop starting — checking every {CHECK_INTERVAL}s "
          f"(drift window={DRIFT_WINDOW}, retrain n_recent={RETRAIN_N_RECENT}, "
          f"gate min_samples={GATE_MIN_SAMPLES})")
    cycle()  # run once immediately instead of waiting for the first interval
    scheduler.start()


if __name__ == "__main__":
    main()
