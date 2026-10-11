"""
Admin API protection.

Every /admin/* endpoint (dashboard buttons, set_shadow, promote, ...) requires
an API key sent in the `X-API-Key` header. Configure it with environment
variables on every process that talks to the admin API (serving, automation
loop, retrain pipeline):

    ADMIN_API_KEY=<long random string>     the key (required in normal use)
    ADMIN_AUTH_DISABLED=1                  explicit opt-out for local dev ONLY

Behaviour:
  - key set            -> requests must send a matching X-API-Key, else 401
  - key unset, no opt-out -> admin API is locked: 503 with instructions
    (fails closed, so forgetting to configure it never leaves the API open)
  - ADMIN_AUTH_DISABLED=1 -> no check (local development only)

Why a custom header matters: browsers only send non-standard headers
cross-site after a CORS preflight, and the CORS policy in serving/app.py only
allows the dashboard's origin. So another website open in your browser can't
trigger these endpoints even though the API is on localhost.

Note this protects the API from other sites and other machines; it is a
shared secret, not per-user login.
"""
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

# Values from .env.example that must never be accepted as a real key.
PLACEHOLDER_KEYS = {"", "change-me", "changeme", "your_admin_api_key_here"}


def _configured_key() -> Optional[str]:
    key = os.environ.get("ADMIN_API_KEY", "").strip()
    return None if key.lower() in PLACEHOLDER_KEYS else key


def auth_mode() -> str:
    """'key' | 'disabled' | 'locked' — used for the startup log line."""
    if _configured_key():
        return "key"
    if os.environ.get("ADMIN_AUTH_DISABLED") == "1":
        return "disabled"
    return "locked"


def require_admin(x_api_key: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency applied to the whole /admin router."""
    expected = _configured_key()
    if expected:
        if not x_api_key or not secrets.compare_digest(
            x_api_key.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(401, "Invalid or missing admin API key (send it in the X-API-Key header).")
        return
    if os.environ.get("ADMIN_AUTH_DISABLED") == "1":
        return
    raise HTTPException(
        503,
        "Admin API is locked: no ADMIN_API_KEY is configured on the server. "
        "Set ADMIN_API_KEY (or ADMIN_AUTH_DISABLED=1 for local development only) and restart.",
    )


def admin_headers() -> dict:
    """Headers for this project's own scripts that call /admin/* (e.g. the retrain pipeline)."""
    key = _configured_key()
    return {"X-API-Key": key} if key else {}
