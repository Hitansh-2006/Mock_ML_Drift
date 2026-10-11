"""Test setup: a throwaway SQLite DB and a known admin key, set BEFORE the app is imported."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="drift_watchdog_tests_")
os.environ["DRIFT_WATCHDOG_DB_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ADMIN_API_KEY"] = "test-key-123"
