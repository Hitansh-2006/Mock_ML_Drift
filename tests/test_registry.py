"""
BUG-1 / BUG-7 / FR-1 / FR-4 / FR-5: the DB (model_registry), not process memory,
decides which model is production and which is shadow.
"""
from tests.conftest import H, add_promotion_request


def _status(w):
    return w.client.get("/status").json()


def _predict_version(w):
    r = w.client.post("/predict", json={"features": w.features})
    assert r.status_code == 200, r.text
    return r.json()["model_version"]


def _set_shadow(w, version):
    return w.client.post("/admin/set_shadow", json={"version": version}, headers=H)


def _decide(w, request_id, decision):
    return w.client.post("/admin/promote", headers=H, json={
        "promotion_request_id": request_id, "decision": decision, "decided_by": "test"})


# ---------------------------------------------------------------- BUG-1: the restart bug
def test_restart_keeps_an_approved_promotion(world):
    assert _set_shadow(world, "v2").status_code == 200
    req = add_promotion_request("v2")
    assert _decide(world, req, "approve").status_code == 200
    assert _status(world)["production_version"] == "v2"

    world.restart()

    assert _status(world) == {"production_version": "v2", "shadow_version": None, "baseline_ready": True}
    assert _predict_version(world) == "v2"


def test_restart_keeps_the_shadow(world):
    assert _set_shadow(world, "v2").status_code == 200
    world.restart()
    assert _status(world)["shadow_version"] == "v2"
    assert _status(world)["production_version"] == "v1"


def test_a_fresh_install_starts_with_v1_in_production_and_no_shadow(world):
    assert _status(world) == {"production_version": "v1", "shadow_version": None, "baseline_ready": True}
    assert _roles() == {"v1": "production"}


# ---------------------------------------------------------------- set_shadow
def _roles():
    from serving.db import SessionLocal, ModelRegistry
    db = SessionLocal()
    try:
        return {r.version: r.role for r in db.query(ModelRegistry).all()}
    finally:
        db.close()


def test_second_set_shadow_archives_the_first(world):
    assert _set_shadow(world, "v2").status_code == 200
    assert _set_shadow(world, "v3").status_code == 200
    assert _roles() == {"v1": "production", "v2": "archived", "v3": "shadow"}
    assert _status(world)["shadow_version"] == "v3"


def test_set_shadow_checks_the_model_files_exist(world):
    assert _set_shadow(world, "v9").status_code == 404
    assert _roles() == {"v1": "production"}


def test_production_cannot_be_set_as_shadow(world):
    assert _set_shadow(world, "v1").status_code == 409


def test_a_replaced_shadow_withdraws_its_pending_request(world):
    from serving.db import SessionLocal, PromotionRequest
    _set_shadow(world, "v2")
    req = add_promotion_request("v2")
    _set_shadow(world, "v3")
    db = SessionLocal()
    try:
        assert db.get(PromotionRequest, req).status == "withdrawn"
    finally:
        db.close()


# ---------------------------------------------------------------- approve / reject
def test_approve_archives_old_production_and_clears_shadow(world):
    _set_shadow(world, "v2")
    req = add_promotion_request("v2")
    body = _decide(world, req, "approve").json()
    assert (body["production_version"], body["shadow_version"]) == ("v2", None)
    assert _roles() == {"v1": "archived", "v2": "production"}


def test_reject_clears_shadow_and_allows_retrain_again(world, monkeypatch):
    from retrain import retrain_pipeline
    _set_shadow(world, "v2")
    req = add_promotion_request("v2")
    assert world.client.post("/admin/retrain", headers=H).status_code == 409   # blocked while v2 is in shadow

    assert _decide(world, req, "reject").status_code == 200

    assert _status(world) == {"production_version": "v1", "shadow_version": None, "baseline_ready": True}
    assert _roles() == {"v1": "production", "v2": "rejected"}
    calls = []
    monkeypatch.setattr(retrain_pipeline, "run_retrain", lambda n_recent: calls.append(n_recent))
    assert world.client.post("/admin/retrain", headers=H).status_code == 200
    assert calls == [500]
    world.restart()
    assert _status(world)["shadow_version"] is None  # and it stays cleared after a restart


def test_a_rejected_version_cannot_be_put_back_into_shadow(world):
    _set_shadow(world, "v2")
    _decide(world, add_promotion_request("v2"), "reject")
    assert _set_shadow(world, "v2").status_code == 409


def test_approve_with_an_unloadable_model_changes_nothing(world):
    from serving.db import SessionLocal, PromotionRequest
    assert _set_shadow(world, "v2").status_code == 200
    req = add_promotion_request("v2")
    (world.models / "model_v2.joblib").write_bytes(b"this is not a joblib file")
    world.app.STATE["models"].pop("v2", None)   # as after a restart: it has to be read from disk again

    r = _decide(world, req, "approve")

    assert r.status_code == 500 and "v2" in r.json()["detail"]
    assert _roles() == {"v1": "production", "v2": "shadow"}
    db = SessionLocal()
    try:
        promo = db.get(PromotionRequest, req)
        assert (promo.status, promo.decided_at, promo.decided_by) == ("pending", None, None)
    finally:
        db.close()
    assert _status(world)["production_version"] == "v1" and _status(world)["shadow_version"] == "v2"
    assert _predict_version(world) == "v1"


def test_a_failing_registry_commit_rolls_back_and_leaves_the_cache_alone(world, monkeypatch):
    from serving import registry
    _set_shadow(world, "v2")
    req = add_promotion_request("v2")
    real = registry.check_invariants

    def boom(db):
        real(db)
        raise RuntimeError("disk full")
    monkeypatch.setattr(registry, "check_invariants", boom)
    try:
        world.client.post("/admin/promote", headers=H, json={"promotion_request_id": req, "decision": "approve"})
    except RuntimeError:
        pass  # TestClient re-raises server errors
    monkeypatch.setattr(registry, "check_invariants", real)
    assert _roles() == {"v1": "production", "v2": "shadow"}
    assert _status(world)["production_version"] == "v1"


def test_stale_request_cannot_be_approved(world):
    _set_shadow(world, "v2")
    stale = add_promotion_request("v3")          # a request for a challenger that is not the shadow
    r = _decide(world, stale, "approve")
    assert r.status_code == 409 and "stale" in r.json()["detail"]
    assert _roles() == {"v1": "production", "v2": "shadow"}
    assert _status(world)["production_version"] == "v1"


def test_double_approve_is_refused(world):
    _set_shadow(world, "v2")
    req = add_promotion_request("v2")
    assert _decide(world, req, "approve").status_code == 200
    assert _decide(world, req, "approve").status_code == 400


# ---------------------------------------------------------------- legacy DBs
def _wipe_registry_only():
    from serving.db import SessionLocal, ModelRegistry
    db = SessionLocal()
    try:
        db.query(ModelRegistry).delete()
        db.commit()
    finally:
        db.close()


def test_legacy_db_with_an_approval_is_seeded_with_that_production(world):
    add_promotion_request("v2", status="approved")
    newest = add_promotion_request("v3", status="approved")
    from serving.db import SessionLocal, PromotionRequest
    db = SessionLocal()
    try:
        db.get(PromotionRequest, newest).decided_at = 2_000_000_000.0
        db.commit()
    finally:
        db.close()
    _wipe_registry_only()          # simulate the DB as it was before the registry existed

    world.restart()

    assert _status(world)["production_version"] == "v3"
    assert _roles() == {"v3": "production", "v1": "archived"}
    assert _predict_version(world) == "v3"


def test_legacy_db_without_approvals_is_seeded_with_v1(world):
    add_promotion_request("v2", status="rejected")
    add_promotion_request("v3", status="pending")
    _wipe_registry_only()
    world.restart()
    assert _roles() == {"v1": "production"}


def test_seeding_never_runs_over_an_existing_registry(world):
    _set_shadow(world, "v2")
    add_promotion_request("v3", status="approved")
    world.restart()
    assert _roles() == {"v1": "production", "v2": "shadow"}


# ---------------------------------------------------------------- the invariant
def test_exactly_one_production_through_a_whole_lifecycle(world):
    def n_production():
        return sum(role == "production" for role in _roles().values())

    assert n_production() == 1
    _set_shadow(world, "v2"); assert n_production() == 1
    _set_shadow(world, "v3"); assert n_production() == 1
    _decide(world, add_promotion_request("v3"), "approve"); assert n_production() == 1
    _set_shadow(world, "v2"); assert n_production() == 1
    _decide(world, add_promotion_request("v2", production="v3"), "reject"); assert n_production() == 1
    world.restart(); assert n_production() == 1
    assert sum(role == "shadow" for role in _roles().values()) == 0


def test_the_database_itself_refuses_a_second_production_or_shadow(world):
    import pytest
    from sqlalchemy.exc import IntegrityError
    from serving.db import SessionLocal, ModelRegistry
    _set_shadow(world, "v2")
    for role in ("production", "shadow"):
        db = SessionLocal()
        try:
            db.add(ModelRegistry(version=f"dup-{role}", role=role))
            with pytest.raises(IntegrityError):
                db.commit()
        finally:
            db.rollback()
            db.close()
