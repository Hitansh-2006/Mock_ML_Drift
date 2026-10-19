"""
Accounts and sessions for the dashboard (standard library only).

  - Passwords: PBKDF2-HMAC-SHA256, random salt, constant-time compare. An unknown
    username still costs one hash, so response time doesn't reveal which names exist.
  - Sessions: a random bearer token; only its SHA-256 is stored. Guests get a short,
    read-only one. Roles: `admin` (everything) and `viewer` (read-only).
  - Throttle: MAX_FAILURES bad logins for a username (or from one client address)
    lock further attempts for LOCKOUT_SECONDS. In-memory, i.e. per serving process
    (the project already assumes a single worker).

Nothing here decides what a role may do: serving/security.py does that, in one place.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from serving.db import AuthSession, User

ROLES = ("admin", "viewer")
MIN_PASSWORD_LENGTH = 8
# Example values that must never become a real account's password.
PLACEHOLDER_PASSWORDS = {"change-me", "changeme", "change-me-please", "your_password_here", "password", "12345678"}
SESSION_SECONDS = 12 * 3600
GUEST_SESSION_SECONDS = 2 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
_HASH_NAME = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 240_000


@dataclass(frozen=True)
class Principal:
    username: str
    role: str                 # admin | viewer
    via: str                  # session | api_key | dev
    guest: bool = False


def _now() -> float:
    return time.time()


def _iterations() -> int:
    return int(os.environ.get("PASSWORD_HASH_ITERATIONS", _DEFAULT_ITERATIONS))


# --------------------------------------------------------------- passwords
def hash_password(password: str, iterations: Optional[int] = None) -> str:
    iterations = iterations or _iterations()
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{_HASH_NAME}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        name, iterations, salt, expected = stored.split("$")
        if name != _HASH_NAME:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = None


def _burn_a_hash(password: str) -> None:
    """Same cost as a real check, for usernames that don't exist."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-real-password")
    verify_password(password, _DUMMY_HASH)


# --------------------------------------------------------------- users
def _normalize(username: str) -> str:
    return (username or "").strip().lower()


def create_user(db: Session, username: str, password: str, role: str) -> User:
    username = _normalize(username)
    if not username:
        raise ValueError("username is required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if db.get(User, username) is not None:
        raise ValueError(f"user '{username}' already exists")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return user


def set_password(db: Session, username: str, password: str) -> None:
    user = db.get(User, _normalize(username))
    if user is None:
        raise ValueError(f"no such user '{username}'")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    user.password_hash = hash_password(password)
    db.query(AuthSession).filter(AuthSession.username == user.username).delete()   # sign out everywhere
    db.commit()


def delete_user(db: Session, username: str) -> bool:
    username = _normalize(username)
    db.query(AuthSession).filter(AuthSession.username == username).delete()
    removed = db.query(User).filter(User.username == username).delete()
    db.commit()
    return bool(removed)


def has_users(db: Session) -> bool:
    return db.query(User).first() is not None


def seed_admin_from_env(db: Session) -> Optional[str]:
    """
    First start: ADMIN_USERNAME + ADMIN_PASSWORD create an admin if that user doesn't exist yet.
    An existing account is never touched, so a password changed later isn't undone by an old env value.
    Returns a message for the startup log, or None.
    """
    username, password = os.environ.get("ADMIN_USERNAME", "").strip(), os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return None
    if db.get(User, _normalize(username)) is not None:
        return None
    if password.strip().lower() in PLACEHOLDER_PASSWORDS:
        return "ADMIN_PASSWORD ignored: that is a placeholder, choose a real password"
    try:
        create_user(db, username, password, "admin")
    except ValueError as e:
        return f"ADMIN_USERNAME/ADMIN_PASSWORD ignored: {e}"
    return f"created admin account '{_normalize(username)}' from ADMIN_USERNAME/ADMIN_PASSWORD"


# --------------------------------------------------------------- throttle
_failures: dict = {}          # key -> (count, locked_until)
_throttle_lock = threading.Lock()


def reset_throttle() -> None:
    with _throttle_lock:
        _failures.clear()


def _keys(username: str, client: str):
    return (f"user:{_normalize(username)}", f"ip:{client}")


def retry_after(username: str, client: str) -> int:
    """Seconds until another attempt is allowed (0 = allowed now)."""
    now = _now()
    with _throttle_lock:
        waits = [max(0, int(_failures[k][1] - now) + 1) for k in _keys(username, client)
                 if k in _failures and _failures[k][1] > now]
    return max(waits, default=0)


def record_failure(username: str, client: str) -> None:
    now = _now()
    with _throttle_lock:
        for key in _keys(username, client):
            count, locked_until = _failures.get(key, (0, 0.0))
            if locked_until and locked_until <= now:     # an expired lockout starts a fresh count
                count, locked_until = 0, 0.0
            count += 1
            if count >= MAX_FAILURES:
                locked_until = now + LOCKOUT_SECONDS
            _failures[key] = (count, locked_until)


def record_success(username: str, client: str) -> None:
    """A good login clears this user's and this client's counters (a shared address isn't punished forever)."""
    with _throttle_lock:
        for key in _keys(username, client):
            _failures.pop(key, None)


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    user = db.get(User, _normalize(username))
    if user is None:
        _burn_a_hash(password or "")
        return None
    return user if verify_password(password or "", user.password_hash) else None


# --------------------------------------------------------------- sessions
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, username: str, role: str, guest: bool = False) -> dict:
    now = _now()
    db.query(AuthSession).filter(AuthSession.expires_at < now).delete()       # tidy expired ones
    token = secrets.token_urlsafe(32)
    expires_at = now + (GUEST_SESSION_SECONDS if guest else SESSION_SECONDS)
    db.add(AuthSession(token_hash=_hash_token(token), username=username, role=role,
                       is_guest=guest, created_at=now, expires_at=expires_at))
    db.commit()
    return {"token": token, "expires_at": expires_at,
            "user": {"username": username, "role": role, "guest": guest}}


def principal_for_token(db: Session, token: str) -> Optional[Principal]:
    if not token:
        return None
    row = db.get(AuthSession, _hash_token(token))
    if row is None:
        return None
    if row.expires_at <= _now():
        db.delete(row)
        db.commit()
        return None
    return Principal(username=row.username, role=row.role, via="session", guest=row.is_guest)


def revoke_session(db: Session, token: str) -> None:
    db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(token)).delete()
    db.commit()
