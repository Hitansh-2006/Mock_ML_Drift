"""
Runs the automation loop inside the serving process so the dashboard can
start and stop it. A BackgroundScheduler fires `loop.cycle` every
`interval_seconds`; `max_instances=1` + `coalesce=True` plus a cycle lock mean
two cycles never overlap, whether they were scheduled or started with run_now.

State that must survive a restart (enabled / interval / last cycle) lives in the
`automation_state` table; on startup `restore()` switches the loop back on if it
was on. This assumes ONE serving process (one uvicorn worker): with several,
each would run its own scheduler.

This module has no way to promote a model - it only runs `loop.cycle`.
"""
import threading
import time
from datetime import timezone
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from automation import loop
from serving.db import SessionLocal, AutomationState

JOB_ID = "automation_cycle"
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 24 * 3600


class AutomationController:
    def __init__(self, deps_factory: Callable, cycle_fn: Callable = None, session_factory=SessionLocal):
        self._deps_factory = deps_factory
        self._cycle_fn = cycle_fn or loop.cycle
        self._session_factory = session_factory
        self._scheduler: Optional[BackgroundScheduler] = None
        self._control_lock = threading.RLock()   # start/stop/restore/shutdown
        self._cycle_lock = threading.Lock()      # held while a cycle runs

    # ------------------------------------------------------------ persistence
    def _read(self) -> dict:
        db = self._session_factory()
        try:
            row = db.get(AutomationState, 1)
            if row is None:
                return {"enabled": False, "interval_seconds": None, "started_at": None,
                        "last_cycle_at": None, "last_cycle": None}
            return {"enabled": row.enabled, "interval_seconds": row.interval_seconds, "started_at": row.started_at,
                    "last_cycle_at": row.last_cycle_at, "last_cycle": row.last_cycle}
        finally:
            db.close()

    def _write(self, **fields) -> None:
        db = self._session_factory()
        try:
            row = db.get(AutomationState, 1)
            if row is None:
                row = AutomationState(id=1, enabled=False)
                db.add(row)
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------ scheduler
    def _is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def _start_scheduler(self, interval_seconds: float) -> None:
        scheduler = BackgroundScheduler(timezone=timezone.utc)
        scheduler.add_job(
            self.run_cycle, IntervalTrigger(seconds=interval_seconds), id=JOB_ID,
            max_instances=1, coalesce=True, replace_existing=True,
            misfire_grace_time=max(1, interval_seconds),
        )
        scheduler.start()
        self._scheduler = scheduler

    def _stop_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    # ------------------------------------------------------------ public API
    def restore(self) -> None:
        """Called at app startup: switch the loop back on if it was on before the restart."""
        with self._control_lock:
            saved = self._read()
            if saved["enabled"] and not self._is_running():
                self._start_scheduler(saved["interval_seconds"] or loop.CHECK_INTERVAL)
                print(f"[automation] restored: running every {saved['interval_seconds'] or loop.CHECK_INTERVAL}s")

    def start(self, interval_seconds: Optional[int] = None) -> dict:
        """Idempotent. A new interval on a running loop reschedules it without a restart."""
        with self._control_lock:
            saved = self._read()
            interval = interval_seconds or saved["interval_seconds"] or loop.CHECK_INTERVAL
            if self._is_running():
                if interval != saved["interval_seconds"]:
                    self._scheduler.reschedule_job(JOB_ID, trigger=IntervalTrigger(seconds=interval))
                    self._write(interval_seconds=interval)
            else:
                self._start_scheduler(interval)
                self._write(enabled=True, interval_seconds=interval, started_at=time.time())
            return self.status()

    def stop(self) -> dict:
        """Idempotent. Turns the loop off for good (until Start); a cycle already running finishes."""
        with self._control_lock:
            self._stop_scheduler()
            self._write(enabled=False, started_at=None)
            return self.status()

    def shutdown(self) -> None:
        """App shutdown: stop the scheduler but keep `enabled`, so the next start restores it."""
        with self._control_lock:
            self._stop_scheduler()

    def status(self) -> dict:
        saved = self._read()
        running = self._is_running()
        job = self._scheduler.get_job(JOB_ID) if running else None
        next_run = job.next_run_time if job is not None else None
        return {
            "running": running,
            "interval_seconds": saved["interval_seconds"] or loop.CHECK_INTERVAL,
            "next_run_at": next_run.timestamp() if next_run else None,
            "last_cycle_at": saved["last_cycle_at"],
            "last_cycle": saved["last_cycle"],
            "started_at": saved["started_at"] if running else None,
            "cycle_in_progress": self._cycle_lock.locked(),
        }

    def run_now(self) -> bool:
        """Starts one cycle in the background right away. False if a cycle is already running."""
        if self._cycle_lock.locked():
            return False
        threading.Thread(target=self.run_cycle, name="automation-run-now", daemon=True).start()
        return True

    # ------------------------------------------------------------ the cycle
    def run_cycle(self) -> Optional[dict]:
        """
        Runs one cycle and records it. Returns None when another cycle is already
        running (skipped, not queued). Nothing that happens inside can raise out
        of here, so a scheduler job never dies.
        """
        if not self._cycle_lock.acquire(blocking=False):
            print("[automation] cycle skipped: the previous one is still running")
            return None
        try:
            started = time.time()
            try:
                result = self._cycle_fn(self._deps_factory())
            except Exception as e:   # loop.cycle already catches; this guards injected/legacy cycle functions
                result = {"drift_checked": False, "drift_triggered": False, "action": "none",
                          "gate_result": None, "error": f"{type(e).__name__}: {e}"}
                result["message"] = f"Error: {result['error']}"
            result["started_at"], result["finished_at"] = started, time.time()
            try:
                self._write(last_cycle_at=result["finished_at"], last_cycle=result)
            except Exception as e:
                print(f"[automation] could not record the cycle result: {e}")
            return result
        finally:
            self._cycle_lock.release()
