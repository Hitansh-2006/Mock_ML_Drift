"""
Automation loop as a dashboard button: a BackgroundScheduler inside the serving
process, controlled through /admin/automation*, one shared cycle implementation
(automation/loop.py) for both the button and `python -m automation.loop`.
"""
import ast
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import H, ROOT, add_promotion_request, wipe_db

OK_CYCLE = {"drift_checked": True, "drift_triggered": False, "action": "none",
            "gate_result": None, "error": None, "message": "No drift"}


def wait_until(predicate, timeout=5.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def auto(world, monkeypatch):
    """The app's real controller with a fake cycle, shut down (and the DB wiped) afterwards."""
    ctl = world.app.AUTOMATION
    calls = []
    monkeypatch.setattr(ctl, "_cycle_fn", lambda deps: calls.append(1) or dict(OK_CYCLE))
    world.calls = calls
    yield world
    ctl.shutdown()


def _get(w):
    return w.client.get("/admin/automation", headers=H).json()


def _start(w, **body):
    return w.client.post("/admin/automation/start", headers=H, json=body or None)


def _stop(w):
    return w.client.post("/admin/automation/stop", headers=H)


# ---------------------------------------------------------------- routes + auth
AUTOMATION_ROUTES = [("GET", "/admin/automation"), ("POST", "/admin/automation/start"),
                     ("POST", "/admin/automation/stop"), ("POST", "/admin/automation/run_now")]


def test_automation_routes_exist_and_reject_unauthenticated_calls(world):
    from tests.test_admin_fixes import _admin_routes  # the OpenAPI-driven enumeration used for I3
    listed = _admin_routes()
    for method, path in AUTOMATION_ROUTES:
        assert (method, path) in listed, f"{method} {path} missing from the OpenAPI schema"
        assert world.client.request(method, path).status_code == 401
        assert world.client.request(method, path, headers={"X-API-Key": "wrong"}).status_code == 401


# ---------------------------------------------------------------- start / stop / status
def test_status_when_never_started(auto):
    s = _get(auto)
    assert s["running"] is False and s["next_run_at"] is None and s["started_at"] is None
    assert s["last_cycle"] is None and s["last_cycle_at"] is None
    assert s["interval_seconds"] == 60


def test_start_is_idempotent(auto):
    first = _start(auto, interval_seconds=120)
    assert first.status_code == 200 and first.json()["running"] is True
    second = _start(auto)           # no body: keep running as-is
    third = _start(auto, interval_seconds=120)   # same interval: also a no-op
    for again in (second, third):
        assert again.json()["started_at"] == first.json()["started_at"]
        assert again.json()["next_run_at"] == first.json()["next_run_at"]
    assert len(auto.app.AUTOMATION._scheduler.get_jobs()) == 1


def test_stop_is_idempotent_even_if_never_started(auto):
    assert _stop(auto).status_code == 200
    _start(auto, interval_seconds=120)
    assert _stop(auto).json()["running"] is False
    assert _stop(auto).json()["running"] is False
    assert _get(auto)["running"] is False


def test_status_reflects_start_interval_change_and_stop(auto):
    before = time.time()
    s = _start(auto, interval_seconds=120).json()
    assert s["running"] and s["interval_seconds"] == 120
    assert before + 100 < s["next_run_at"] < before + 125       # first run one interval from now
    assert before - 1 <= s["started_at"] <= time.time()

    changed = _start(auto, interval_seconds=300).json()          # changing the interval reschedules, same run
    assert changed["interval_seconds"] == 300 and changed["started_at"] == s["started_at"]
    assert changed["next_run_at"] > before + 280

    stopped = _stop(auto).json()
    assert stopped["running"] is False and stopped["next_run_at"] is None
    assert stopped["interval_seconds"] == 300                    # remembered for the next start


def test_interval_is_validated(auto):
    assert _start(auto, interval_seconds=1).status_code == 422
    assert _start(auto, interval_seconds=10 ** 7).status_code == 422
    assert _get(auto)["running"] is False


def test_run_now_records_the_cycle_and_refuses_a_second_one_while_busy(auto):
    gate = threading.Event()
    ctl = auto.app.AUTOMATION
    ctl._cycle_fn = lambda deps: gate.wait(5) and dict(OK_CYCLE, message="slow")
    assert auto.client.post("/admin/automation/run_now", headers=H).status_code == 200
    assert wait_until(lambda: _get(auto)["cycle_in_progress"])
    assert auto.client.post("/admin/automation/run_now", headers=H).status_code == 409
    gate.set()
    assert wait_until(lambda: _get(auto)["last_cycle"] is not None)
    last = _get(auto)["last_cycle"]
    assert last["message"] == "slow" and _get(auto)["last_cycle_at"] is not None
    assert _get(auto)["running"] is False   # run_now doesn't turn the loop on


# ---------------------------------------------------------------- restart
def test_enabled_state_is_restored_after_a_restart(world, monkeypatch):
    ctl = world.app.AUTOMATION
    monkeypatch.setattr(ctl, "_cycle_fn", lambda deps: dict(OK_CYCLE))
    try:
        with TestClient(world.app.app) as first_process:
            assert first_process.post("/admin/automation/start", headers=H, json={"interval_seconds": 90}).status_code == 200
        assert ctl._scheduler is None or not ctl._scheduler.running   # lifespan shutdown stopped the scheduler
        world.restart()
        with TestClient(world.app.app) as second_process:
            s = second_process.get("/admin/automation", headers=H).json()
            assert s["running"] is True and s["interval_seconds"] == 90 and s["next_run_at"] is not None
            second_process.post("/admin/automation/stop", headers=H)
        world.restart()
        with TestClient(world.app.app) as third_process:
            assert third_process.get("/admin/automation", headers=H).json()["running"] is False  # an explicit stop sticks
    finally:
        ctl.shutdown()


# ---------------------------------------------------------------- scheduler robustness
def _controller(cycle_fn):
    from automation.controller import AutomationController
    return AutomationController(deps_factory=lambda: None, cycle_fn=cycle_fn)


def test_an_exception_in_a_cycle_is_recorded_and_the_scheduler_keeps_running(world):
    calls = []

    def cycle(deps):
        calls.append(1)
        raise RuntimeError("boom")

    ctl = _controller(cycle)
    try:
        ctl._start_scheduler(0.1)
        assert wait_until(lambda: len(calls) >= 3), "scheduler stopped firing after the first exception"
        status = ctl.status()
        assert status["running"] is True
        assert "boom" in status["last_cycle"]["error"]
        assert status["last_cycle"]["action"] == "none"
    finally:
        ctl.shutdown()


def test_cycles_never_overlap(world):
    state = {"now": 0, "max": 0, "runs": 0}
    lock = threading.Lock()

    def slow(deps):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            state["runs"] += 1
        time.sleep(0.4)
        with lock:
            state["now"] -= 1
        return dict(OK_CYCLE)

    ctl = _controller(slow)
    try:
        ctl._start_scheduler(0.05)     # fires far more often than a cycle takes
        assert wait_until(lambda: state["runs"] >= 2, timeout=6)
        assert state["max"] == 1
        # and a manual run while one is in flight is refused rather than stacked
        assert wait_until(lambda: ctl.status()["cycle_in_progress"])
        assert ctl.run_now() is False
    finally:
        ctl.shutdown()


def test_two_concurrent_run_cycle_calls_only_run_once(world):
    started, release, runs = threading.Event(), threading.Event(), []

    def cycle(deps):
        runs.append(1)
        started.set()
        release.wait(5)
        return dict(OK_CYCLE)

    ctl = _controller(cycle)
    t = threading.Thread(target=ctl.run_cycle)
    t.start()
    assert started.wait(5)
    assert ctl.run_cycle() is None          # skipped: one is already running
    release.set()
    t.join(5)
    assert runs == [1]


# ---------------------------------------------------------------- the cycle itself
@pytest.fixture
def cyc(world, monkeypatch):
    """loop.cycle() wired like the in-process loop, with drift/retrain stubbed."""
    from automation import loop
    ns = world
    ns.loop = loop
    ns.retrains = []
    ns.drift = {"value": None}
    monkeypatch.setattr(loop, "check_drift", lambda window: ns.drift["value"])

    def fake_retrain(n_recent, deploy=None):
        ns.retrains.append(n_recent)
        return "v2"
    monkeypatch.setattr(loop, "run_retrain", fake_retrain)
    ns.deps = loop.CycleDeps(get_status=ns.app.status, deploy_shadow=lambda v: None,
                             retrain_lock=ns.app.RETRAIN_LOCK)
    return ns


def test_cycle_returns_a_structured_result(cyc):
    cyc.drift["value"] = {"triggered": False, "overall_score": 0.05, "n_drifted_features": 0}
    r = cyc.loop.cycle(cyc.deps)
    assert {"drift_checked", "drift_triggered", "action", "gate_result", "error", "message"} <= set(r)
    assert (r["drift_checked"], r["drift_triggered"], r["action"], r["error"]) == (True, False, "none", None)
    assert r["message"] == "No drift"


def test_cycle_without_enough_traffic_says_so(cyc):
    r = cyc.loop.cycle(cyc.deps)           # check_drift returned None
    assert r["drift_checked"] is False and r["action"] == "none" and "Waiting" in r["message"]


def test_cycle_that_hits_an_exception_reports_it_instead_of_raising(cyc, monkeypatch):
    monkeypatch.setattr(cyc.loop, "check_drift", lambda w: (_ for _ in ()).throw(ValueError("bad window")))
    r = cyc.loop.cycle(cyc.deps)
    assert "bad window" in r["error"] and r["action"] == "none"


def test_cycle_retrains_on_drift_and_releases_the_shared_lock(cyc):
    cyc.drift["value"] = {"triggered": True, "overall_score": 0.4, "n_drifted_features": 3}
    r = cyc.loop.cycle(cyc.deps)
    assert cyc.retrains == [500] and r["action"] == "retrain" and "v2" in r["message"]
    assert not cyc.app.RETRAIN_LOCK.locked()


def test_cycle_skips_retraining_while_the_button_s_retrain_holds_the_lock(cyc):
    cyc.drift["value"] = {"triggered": True, "overall_score": 0.4, "n_drifted_features": 3}
    assert cyc.app.RETRAIN_LOCK.acquire(blocking=False)
    try:
        r = cyc.loop.cycle(cyc.deps)
        assert cyc.retrains == [] and r["action"] == "none"
        assert r["retrain_skipped"] is True and "already running" in r["message"]
        assert cyc.app.RETRAIN_LOCK.locked()     # the cycle must not release someone else's lock
    finally:
        cyc.app.RETRAIN_LOCK.release()


def test_retrain_button_is_refused_while_a_cycle_retrains(cyc, monkeypatch):
    cyc.drift["value"] = {"triggered": True, "overall_score": 0.4, "n_drifted_features": 3}
    inside, release = threading.Event(), threading.Event()

    def blocking_retrain(n_recent, deploy=None):
        inside.set()
        release.wait(5)
        return None
    monkeypatch.setattr(cyc.loop, "run_retrain", blocking_retrain)
    t = threading.Thread(target=cyc.loop.cycle, args=(cyc.deps,))
    t.start()
    try:
        assert inside.wait(5)
        r = cyc.client.post("/admin/retrain", headers=H)
        assert r.status_code == 409 and "already running" in r.json()["detail"]
    finally:
        release.set()
        t.join(5)
    assert not cyc.app.RETRAIN_LOCK.locked()


def test_cycle_releases_the_lock_when_retraining_fails(cyc, monkeypatch):
    cyc.drift["value"] = {"triggered": True, "overall_score": 0.4, "n_drifted_features": 3}
    monkeypatch.setattr(cyc.loop, "run_retrain", lambda n, deploy=None: (_ for _ in ()).throw(RuntimeError("no disk")))
    r = cyc.loop.cycle(cyc.deps)
    assert "no disk" in r["error"] and not cyc.app.RETRAIN_LOCK.locked()


def test_cycle_does_not_retrain_while_a_challenger_is_already_in_shadow(cyc):
    cyc.client.post("/admin/set_shadow", json={"version": "v2"}, headers=H)
    cyc.drift["value"] = {"triggered": True, "overall_score": 0.4, "n_drifted_features": 3}
    r = cyc.loop.cycle(cyc.deps)
    assert cyc.retrains == [] and r["action"] == "gate" and r["drift_triggered"] is True


def _log_pairs(n, challenger_better=True):
    """n paired production(v1)/shadow(v2) rows with labels: the challenger errs by 5-10k, production by 20k.
    Pairs share a request_id and carry NO timestep, like real traffic."""
    from serving.db import SessionLocal, PredictionLog
    db = SessionLocal()
    try:
        for t in range(1, n + 1):
            true = 300_000.0
            shadow_err = 5_000.0 + 100.0 * t if challenger_better else 30_000.0 + 100.0 * t
            for version, is_shadow, err in (("v1", False, 20_000.0), ("v2", True, shadow_err)):
                db.add(PredictionLog(request_id=f"req-{t}", features={}, prediction=true + err, model_version=version,
                                     is_shadow=is_shadow, true_label=true, latency_ms=5.0))
        db.commit()
    finally:
        db.close()


def test_cycle_reports_waiting_for_paired_labels(cyc):
    cyc.client.post("/admin/set_shadow", json={"version": "v2"}, headers=H)
    _log_pairs(12)
    r = cyc.loop.cycle(cyc.deps)
    assert r["action"] == "gate" and r["gate_result"]["outcome"] == "waiting"
    assert "Waiting for data: 12/30 paired labels" in r["message"]


def test_I1_a_passing_gate_only_creates_a_pending_request_and_never_changes_production(cyc):
    from serving.db import SessionLocal, PromotionRequest
    from serving import registry
    cyc.client.post("/admin/set_shadow", json={"version": "v2"}, headers=H)
    _log_pairs(40)

    r = cyc.loop.cycle(cyc.deps)

    assert r["action"] == "gate" and r["gate_result"]["outcome"] == "request_created"
    db = SessionLocal()
    try:
        rows = db.query(PromotionRequest).all()
        assert [(x.status, x.challenger_version) for x in rows] == [("pending", "v2")]
        assert registry.current_roles(db) == ("v1", "v2")      # production untouched, shadow still shadow
    finally:
        db.close()
    assert cyc.client.get("/status").json()["production_version"] == "v1"
    cyc.restart()
    assert cyc.client.get("/status").json()["production_version"] == "v1"
    # a second cycle with no new labels updates nothing and still promotes nothing
    assert cyc.loop.cycle(cyc.deps)["gate_result"]["outcome"] == "no_new_data"


def test_I1_automation_code_has_no_path_to_promotion():
    """AST scan (docstrings excluded): the loop, controller, gate, retrain and monitor never reference promotion."""
    banned_names = {"promote", "decide_promotion", "assert_promotable"}
    for rel in ("automation/loop.py", "automation/controller.py", "evaluation/evaluation_gate.py",
                "retrain/retrain_pipeline.py", "monitor/drift_monitor.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name.split(".")[-1] for a in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if "admin/promote" in node.value:
                    pytest.fail(f"{rel} calls the promote endpoint: {node.value!r}")
            hit = banned_names.intersection(names)
            if hit:
                pytest.fail(f"{rel} references {sorted(hit)}: automation must not be able to promote")

def test_the_cli_cycle_survives_an_unreachable_api(monkeypatch):
    import requests
    from automation import loop

    def unreachable(*a, **k):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(loop.requests, "get", unreachable)
    r = loop.cycle()                       # default (HTTP) deps, as `python -m automation.loop` uses
    assert r["error"] and "unreachable" in r["error"] and r["action"] == "none"
