"""
Who may call /admin/*, and what each kind of caller may do. This is the ONE place
that decides it: every /admin route hangs off the `admin` router in serving/app.py,
which depends on `require_admin` below, so a new route cannot forget the check.

Credentials (any one is enough):
  - a session: `Authorization: Bearer <token>` from POST /auth/login or /auth/guest
  - the optional machine key: `X-API-Key: <ADMIN_API_KEY>`, for this project's own
    scripts (headless retrain / automation loop). The dashboard doesn't use it.

What a caller may do:
  - role `admin` (a signed-in admin, or the machine key): everything
  - role `viewer` (a read-only account or a guest): GET only. Every other method is
    refused with 403, so nothing a viewer does can change production (invariant I1).

Behaviour:
  - valid credentials              -> allowed (viewers only for GET)
  - none / invalid                 -> 401
  - nothing configured at all      -> 503 (fails closed: no ADMIN_API_KEY and no accounts)
  - ADMIN_AUTH_DISABLED=1 with no key -> no check (local development only)

Note this protects the API from other sites and other machines; it is a small
single-operator login, not an identity platform.
"""
import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from serving import auth
from serving.db import get_session

# Values from .env.example that must never be accepted as a real key.
PLACEHOLDER_KEYS = {"", "change-me", "changeme", "your_admin_api_key_here"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _configured_key() -> Optional[str]:
    key = os.environ.get("ADMIN_API_KEY", "").strip()
    return None if key.lower() in PLACEHOLDER_KEYS else key


def _auth_disabled() -> bool:
    return os.environ.get("ADMIN_AUTH_DISABLED") == "1"


def guest_enabled() -> bool:
    return os.environ.get("GUEST_LOGIN_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def is_configured(db: Session) -> bool:
    """True once there is at least one way to sign in (an account or the machine key)."""
    return bool(_configured_key()) or auth.has_users(db)


def auth_mode(db: Optional[Session] = None) -> str:
    """'accounts' | 'key' | 'both' | 'disabled' | 'locked' - for the startup log line."""
    key = bool(_configured_key())
    accounts = auth.has_users(db) if db is not None else False
    if key and accounts:
        return "both"
    if accounts:
        return "accounts"
    if key:
        return "key"
    return "disabled" if _auth_disabled() else "locked"


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip() or None
    return None


def current_principal(db: Session, x_api_key: Optional[str], authorization: Optional[str]) -> Optional[auth.Principal]:
    expected = _configured_key()
    if expected and x_api_key and secrets.compare_digest(x_api_key.encode("utf-8"), expected.encode("utf-8")):
        return auth.Principal(username="api-key", role="admin", via="api_key")
    token = _bearer(authorization)
    if token:
        return auth.principal_for_token(db, token)
    return None


def require_admin(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_session),
) -> auth.Principal:
    """FastAPI dependency applied to the whole /admin router."""
    principal = current_principal(db, x_api_key, authorization)

    if principal is None:
        if _auth_disabled() and not _configured_key():
            principal = auth.Principal(username="dev", role="admin", via="dev")
        elif not is_configured(db):
            raise HTTPException(
                503,
                "Admin API is locked: no account or ADMIN_API_KEY is configured on the server. "
                "Set ADMIN_USERNAME and ADMIN_PASSWORD (or run `python -m serving.manage_users create`), "
                "or ADMIN_AUTH_DISABLED=1 for local development only, and restart.",
            )
        else:
            raise HTTPException(401, "Sign in required (missing, invalid or expired session).",
                                headers={"WWW-Authenticate": "Bearer"})

    if principal.role != "admin" and request.method not in SAFE_METHODS:
        raise HTTPException(403, "This account is read-only. Sign in with an admin account to make changes.")
    return principal


def admin_headers() -> dict:
    """Headers for this project's own scripts that call /admin/* (e.g. the retrain pipeline)."""
    key = _configured_key()
    return {"X-API-Key": key} if key else {}
