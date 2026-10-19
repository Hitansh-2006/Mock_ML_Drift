"""
Loads the project's `.env` file into the environment (no extra dependency).

Docker Compose already reads `.env`; this makes a plain local `uvicorn ...` start
read the same file, so ADMIN_USERNAME / ADMIN_PASSWORD / etc. work without
exporting them by hand. Variables that are already set in the real environment
win, and it can be switched off with DRIFT_WATCHDOG_NO_DOTENV=1 (the tests do).

Imported for its side effect by serving/db.py, which every entry point
(API, retrain, gate, loop, scripts) imports before it reads any setting.
"""
import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path = ENV_PATH) -> int:
    """Set variables from `path` that are not already set. Returns how many were set."""
    if os.environ.get("DRIFT_WATCHDOG_NO_DOTENV") == "1" or not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value
            loaded += 1
    return loaded


load_dotenv()
