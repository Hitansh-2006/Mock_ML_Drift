"""
Username/password login + read-only guest login for the dashboard.

Rules under test:
  - /admin/* needs a valid session (or the optional machine API key); no credentials -> 401
  - a guest (or any viewer) can READ but every non-GET /admin route is 403: nothing a guest
    does can change production (invariant I1), and a new route can't forget the rule
  - passwords are stored hashed, wrong logins look identical, repeated failures are throttled
"""
import pytest

from tests.conftest import H, add_promotion_request, wipe_db

PW = "correct horse battery"


@pytest.fixture
def acct(world, monkeypatch):
    """The app with one admin account (alice) and one read-only viewer (vic); accounts wiped afterwards."""
    from serving import auth
    from serving.db import SessionLocal
    db = SessionLocal()
    try:
        auth.create_user(db, "alice", PW, "admin")
        auth.create_user(db, "vic", PW, "viewer")
    finally:
        db.close()
    auth.reset_throttle()
    world.auth = auth
    yield world
    wipe_db()
    auth.reset_throttle()


def _login(w, username="alice", password=PW):
    return w.client.post("/auth/login", json={"username": username, "password": password})


def _bearer(w, username="alice", password=PW):
    r = _login(w, username, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _guest(w):
    r = w.client.post("/auth/guest")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------- login
def test_login_returns_a_session_that_identifies_the_user(acct):
    r = _login(acct)
    body = r.json()
    assert r.status_code == 200 and body["user"] == {"username": "alice", "role": "admin", "guest": False}
    assert body["token"] and body["expires_at"] > 0
    me = acct.client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["username"] == "alice" and me.json()["role"] == "admin"


def test_usernames_are_case_insensitive_but_passwords_are_not(acct):
    assert _login(acct, "ALICE").status_code == 200
    assert _login(acct, "alice", PW.upper()).status_code == 401


def test_wrong_password_and_unknown_user_are_indistinguishable(acct):
    wrong = _login(acct, "alice", "not the password")
    unknown = _login(acct, "nobody", PW)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_empty_credentials_are_rejected(acct):
    assert acct.client.post("/auth/login", json={"username": "", "password": ""}).status_code in (401, 422)
    assert acct.client.post("/auth/login", json={}).status_code == 422


def test_repeated_failures_lock_the_account_for_a_while(acct, monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(acct.auth, "_now", lambda: clock["t"])
    for _ in range(acct.auth.MAX_FAILURES):
        assert _login(acct, "alice", "bad").status_code == 401
    locked = _login(acct, "alice", PW)                     # even the right password is refused now
    assert locked.status_code == 429 and "Retry-After" in locked.headers
    clock["t"] += acct.auth.LOCKOUT_SECONDS + 1
    assert _login(acct, "alice", PW).status_code == 200    # and works again afterwards


def test_a_successful_login_resets_the_failure_count(acct):
    for _ in range(acct.auth.MAX_FAILURES - 1):
        _login(acct, "alice", "bad")
    assert _login(acct, "alice", PW).status_code == 200
    for _ in range(acct.auth.MAX_FAILURES - 1):
        assert _login(acct, "alice", "bad").status_code == 401   # still not locked


def test_passwords_are_stored_hashed_and_salted(acct):
    from serving.db import SessionLocal, User
    db = SessionLocal()
    try:
        hashes = {u.username: u.password_hash for u in db.query(User).all()}
    finally:
        db.close()
    assert PW not in hashes["alice"] and hashes["alice"].startswith("pbkdf2_sha256$")
    assert hashes["alice"] != hashes["vic"]            # same password, different salt


def test_weak_passwords_cannot_be_set(acct):
    from serving.db import SessionLocal
    db = SessionLocal()
    try:
        with pytest.raises(ValueError):
            acct.auth.create_user(db, "weak", "short", "admin")
        with pytest.raises(ValueError):
            acct.auth.create_user(db, "alice", PW, "admin")       # duplicate
        with pytest.raises(ValueError):
            acct.auth.create_user(db, "boss", PW, "superuser")    # unknown role
    finally:
        db.close()


# ---------------------------------------------------------------- admin session
def test_an_admin_session_can_read_and_change_things(acct):
    headers = _bearer(acct)
    assert acct.client.get("/admin/stats", headers=headers).status_code == 200
    assert acct.client.post("/admin/set_shadow", headers=headers, json={"version": "v2"}).status_code == 200
    assert acct.client.get("/status").json()["shadow_version"] == "v2"


def test_garbage_and_missing_tokens_are_rejected(acct):
    for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}):
        assert acct.client.get("/admin/stats", headers=headers).status_code == 401


def test_logout_revokes_the_session(acct):
    headers = _bearer(acct)
    assert acct.client.get("/admin/stats", headers=headers).status_code == 200
    assert acct.client.post("/auth/logout", headers=headers).status_code == 200
    assert acct.client.get("/admin/stats", headers=headers).status_code == 401
    assert acct.client.get("/auth/me", headers=headers).status_code == 401


def test_sessions_expire(acct, monkeypatch):
    headers = _bearer(acct)
    now = acct.auth._now()
    monkeypatch.setattr(acct.auth, "_now", lambda: now + acct.auth.SESSION_SECONDS + 5)
    assert acct.client.get("/admin/stats", headers=headers).status_code == 401


def test_the_machine_api_key_still_works_for_scripts(acct):
    assert acct.client.get("/admin/stats", headers=H).status_code == 200
    assert acct.client.post("/admin/set_shadow", headers=H, json={"version": "v2"}).status_code == 200


# ---------------------------------------------------------------- guest / viewer: read-only
def _mutating_admin_routes():
    from tests.test_admin_fixes import _admin_routes
    routes = [(m, p) for m, p in _admin_routes() if m != "GET"]
    assert len(routes) >= 8   # set_shadow, promote, retrain, train_baseline, drift check, upload, automation x3
    return routes


def test_guest_login_gives_a_read_only_session(acct):
    body = acct.client.post("/auth/guest").json()
    assert body["user"] == {"username": "guest", "role": "viewer", "guest": True}
    headers = {"Authorization": f"Bearer {body['token']}"}
    for path in ("/admin/stats", "/admin/predictions", "/admin/drift_events", "/admin/promotion_requests", "/admin/automation"):
        assert acct.client.get(path, headers=headers).status_code == 200, path


@pytest.mark.parametrize("who", ["guest", "viewer"])
def test_read_only_sessions_get_403_on_every_mutating_admin_route(acct, who):
    headers = _guest(acct) if who == "guest" else _bearer(acct, "vic")
    for method, path in _mutating_admin_routes():
        r = acct.client.request(method, path, headers=headers)
        assert r.status_code == 403, f"{who} could reach {method} {path} ({r.status_code})"


def test_I1_a_guest_cannot_approve_or_reject_a_promotion(acct):
    acct.client.post("/admin/set_shadow", headers=H, json={"version": "v2"})
    req = add_promotion_request("v2")
    headers = _guest(acct)
    for decision in ("approve", "reject"):
        r = acct.client.post("/admin/promote", headers=headers, json={"promotion_request_id": req, "decision": decision})
        assert r.status_code == 403
    assert acct.client.get("/status").json() == {"production_version": "v1", "shadow_version": "v2", "baseline_ready": True}
    pending = [p for p in acct.client.get("/admin/promotion_requests", headers=headers).json() if p["id"] == req]
    assert pending[0]["status"] == "pending"


def test_guest_login_can_be_switched_off(acct, monkeypatch):
    monkeypatch.setenv("GUEST_LOGIN_ENABLED", "0")
    assert acct.client.post("/auth/guest").status_code == 403
    assert acct.client.get("/auth/config").json()["guest_enabled"] is False


def test_guest_sessions_are_shorter_than_user_sessions(acct):
    guest = acct.client.post("/auth/guest").json()
    user = _login(acct).json()
    assert guest["expires_at"] < user["expires_at"]


# ---------------------------------------------------------------- audit trail: who decided
def _decision_row(w, headers, request_id):
    rows = w.client.get("/admin/promotion_requests", headers=headers).json()
    return next(r for r in rows if r["id"] == request_id)


def test_the_approver_comes_from_the_session_not_from_the_request(acct):
    headers = _bearer(acct)                                     # alice
    acct.client.post("/admin/set_shadow", headers=headers, json={"version": "v2"})
    req = add_promotion_request("v2")
    r = acct.client.post("/admin/promote", headers=headers, json={
        "promotion_request_id": req, "decision": "approve", "decided_by": "mallory"})   # a client can't pick the name
    assert r.status_code == 200
    row = _decision_row(acct, headers, req)
    assert (row["status"], row["decided_by"]) == ("approved", "alice")
    assert row["decided_at"] is not None


def test_a_rejection_records_who_rejected_it(acct):
    headers = _bearer(acct)
    acct.client.post("/admin/set_shadow", headers=headers, json={"version": "v2"})
    req = add_promotion_request("v2")
    acct.client.post("/admin/promote", headers=headers, json={"promotion_request_id": req, "decision": "reject"})
    assert _decision_row(acct, headers, req)["decided_by"] == "alice"


def test_a_decision_made_with_the_machine_key_is_recorded_as_such(acct):
    acct.client.post("/admin/set_shadow", headers=H, json={"version": "v2"})
    req = add_promotion_request("v2")
    acct.client.post("/admin/promote", headers=H, json={"promotion_request_id": req, "decision": "approve", "decided_by": "mallory"})
    assert _decision_row(acct, H, req)["decided_by"] == "api-key"


def test_pending_requests_show_no_decider_yet(acct):
    req = add_promotion_request("v2")
    row = _decision_row(acct, _bearer(acct), req)
    assert row["decided_by"] is None and row["decided_at"] is None


# ---------------------------------------------------------------- config + fail-closed
def test_auth_config_is_public_and_says_whether_login_is_possible(acct):
    cfg = acct.client.get("/auth/config").json()
    assert cfg == {"configured": True, "guest_enabled": True}


def test_with_no_accounts_and_no_key_everything_fails_closed(world, monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_DISABLED", raising=False)
    wipe_db()
    assert world.client.get("/auth/config").json() == {"configured": False, "guest_enabled": True}
    assert world.client.get("/admin/stats").status_code == 503
    assert world.client.post("/auth/guest").status_code == 503
    assert world.client.post("/auth/login", json={"username": "alice", "password": PW}).status_code == 503


# ---------------------------------------------------------------- .env loading
def test_dotenv_loader_sets_missing_values_only(tmp_path, monkeypatch):
    from serving import config
    for name in ("DW_TEST_A", "DW_TEST_B", "DW_TEST_C"):
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)                    # registers cleanup so nothing leaks into other tests
    monkeypatch.setenv("DW_TEST_C", "from-real-environment")
    monkeypatch.delenv("DRIFT_WATCHDOG_NO_DOTENV")
    env = tmp_path / ".env"
    env.write_text('# comment\nDW_TEST_A=plain\nDW_TEST_B="quoted value"\nDW_TEST_C=from-file\n\nnot a pair\n', encoding="utf-8")

    assert config.load_dotenv(env) == 2
    import os
    assert (os.environ["DW_TEST_A"], os.environ["DW_TEST_B"], os.environ["DW_TEST_C"]) == (
        "plain", "quoted value", "from-real-environment")


def test_dotenv_loading_can_be_switched_off(tmp_path):
    from serving import config
    env = tmp_path / ".env"
    env.write_text("DW_TEST_OFF=1\n", encoding="utf-8")
    assert config.load_dotenv(env) == 0            # the test session runs with DRIFT_WATCHDOG_NO_DOTENV=1


# ---------------------------------------------------------------- first account from the environment
def test_the_first_admin_is_seeded_from_the_environment_and_never_overwritten(world, monkeypatch):
    from serving import auth
    from serving.db import SessionLocal
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    world.restart()
    assert _login(world, "boss").status_code == 200

    db = SessionLocal()
    try:
        auth.set_password(db, "boss", "a different long password")
    finally:
        db.close()
    world.restart()    # env still says the old password: it must NOT silently replace the changed one
    assert _login(world, "boss", PW).status_code == 401
    assert _login(world, "boss", "a different long password").status_code == 200
    wipe_db()


@pytest.mark.parametrize("bad", ["short", "change-me", "Change-Me-Please", "password"])
def test_a_weak_or_placeholder_seed_password_is_refused_not_stored(world, monkeypatch, bad):
    from serving.db import SessionLocal, User
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", bad)
    world.restart()
    db = SessionLocal()
    try:
        assert db.query(User).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------- signup
def test_signup_creates_admin_session_and_persists_user(acct):
    r = acct.client.post("/auth/signup", json={"username": "bob", "password": "supersecretpassword"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["username"] == "bob"
    assert body["user"]["role"] == "admin"
    assert body["user"]["guest"] is False
    assert body["token"]

    # Bob can authenticate and access admin routes
    me = acct.client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "bob"
    assert me.json()["role"] == "admin"


def test_signup_rejects_duplicate_username(acct):
    r = acct.client.post("/auth/signup", json={"username": "alice", "password": "supersecretpassword"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_signup_rejects_short_password(acct):
    r = acct.client.post("/auth/signup", json={"username": "carol", "password": "123"})
    assert r.status_code == 400
    assert "at least 8 characters" in r.json()["detail"]
