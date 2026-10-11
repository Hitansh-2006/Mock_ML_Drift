"""
Model registry transitions (FR-1, FR-4, FR-5). Every function here runs one
DB transaction and commits it, or rolls back and leaves the registry exactly
as it was. serving/app.py updates its in-memory cache only AFTER these return,
so the order is always: write the DB first, then the cache.

Invariants (also enforced by partial unique indexes in serving/db.py):
  - exactly one `production` row once the registry is seeded
  - at most one `shadow` row
"""
import json
import time
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from serving.db import ModelRegistry, PromotionRequest

PRODUCTION, SHADOW, ARCHIVED, REJECTED = "production", "shadow", "archived", "rejected"
DEFAULT_PRODUCTION = "v1"


class RegistryError(Exception):
    """A refused transition; carries the HTTP status the API should answer with."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def describe(models_dir: Path, version: str) -> dict:
    """Optional registry columns taken from model_<version>.meta.json (missing/bad file -> empty)."""
    try:
        meta = json.loads((Path(models_dir) / f"model_{version}.meta.json").read_text())
    except (OSError, ValueError):
        return {}
    return {"metrics": meta, "trained_on_rows": meta.get("n_train_rows")}


def _row(db: Session, version: str) -> Optional[ModelRegistry]:
    return db.get(ModelRegistry, version)


def _by_role(db: Session, role: str) -> Optional[ModelRegistry]:
    return db.query(ModelRegistry).filter(ModelRegistry.role == role).first()


def current_roles(db: Session) -> Tuple[Optional[str], Optional[str]]:
    """(production_version, shadow_version) as recorded in the DB."""
    prod, shadow = _by_role(db, PRODUCTION), _by_role(db, SHADOW)
    return (prod.version if prod else None, shadow.version if shadow else None)


def check_invariants(db: Session) -> None:
    n_prod = db.query(ModelRegistry).filter(ModelRegistry.role == PRODUCTION).count()
    n_shadow = db.query(ModelRegistry).filter(ModelRegistry.role == SHADOW).count()
    if n_prod != 1 or n_shadow > 1:
        raise RuntimeError(f"registry invariant violated: {n_prod} production, {n_shadow} shadow rows")


def seed_if_empty(db: Session, models_dir: Path) -> bool:
    """
    First start on a DB that predates the registry: production is the challenger
    of the most recent APPROVED promotion request (so an approval that already
    exists is never lost), otherwise v1. Returns True if it seeded anything.
    """
    if db.query(ModelRegistry).count():
        return False
    approved = (
        db.query(PromotionRequest)
        .filter(PromotionRequest.status == "approved")
        .order_by(PromotionRequest.decided_at.desc(), PromotionRequest.id.desc())
        .first()
    )
    prod_version = approved.challenger_version if approved else DEFAULT_PRODUCTION
    db.add(ModelRegistry(version=prod_version, role=PRODUCTION,
                         promoted_at=approved.decided_at if approved else None,
                         **describe(models_dir, prod_version)))
    if prod_version != DEFAULT_PRODUCTION:
        db.add(ModelRegistry(version=DEFAULT_PRODUCTION, role=ARCHIVED, **describe(models_dir, DEFAULT_PRODUCTION)))
    db.commit()
    return True


def _withdraw_stale_pending(db: Session, shadow_version: Optional[str], exclude_id: Optional[int] = None) -> None:
    """A pending request for a challenger that is no longer the shadow can never be approved; withdraw it."""
    q = db.query(PromotionRequest).filter(PromotionRequest.status == "pending")
    for req in q.all():
        if req.id != exclude_id and req.challenger_version != shadow_version:
            req.status = "withdrawn"


def set_shadow(db: Session, version: str, extra: Optional[dict] = None) -> None:
    """Any existing shadow becomes `archived`; `version` becomes the shadow."""
    try:
        prod = _by_role(db, PRODUCTION)
        if prod is not None and prod.version == version:
            raise RegistryError(409, f"{version} is the production model; it cannot also be the shadow.")
        row = _row(db, version)
        if row is not None and row.role == REJECTED:
            raise RegistryError(409, f"{version} was rejected by a human; train a new challenger instead.")
        old = _by_role(db, SHADOW)
        if old is not None and old.version != version:
            old.role = ARCHIVED
            db.flush()  # free the shadow slot before taking it (unique index)
        if row is None:
            row = ModelRegistry(version=version, role=SHADOW, **(extra or {}))
            db.add(row)
        else:
            row.role = SHADOW
        _withdraw_stale_pending(db, version)
        db.flush()
        check_invariants(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


def assert_promotable(db: Session, promo: PromotionRequest) -> None:
    """Only the challenger currently in the shadow slot can be promoted."""
    _, shadow = current_roles(db)
    if shadow != promo.challenger_version:
        raise RegistryError(
            409, f"stale request: {promo.challenger_version} is not the current shadow "
                 f"({shadow or 'none'}). Withdraw or reject this request.")


def promote(db: Session, promo: PromotionRequest, decided_by: Optional[str]) -> None:
    """
    Approve: ONE transaction marks the request approved, archives the old
    production, makes the challenger production and clears the shadow slot.
    The caller has already loaded the challenger model successfully.
    """
    try:
        assert_promotable(db, promo)
        old = _by_role(db, PRODUCTION)
        if old is not None:
            old.role = ARCHIVED
            db.flush()
        challenger = _row(db, promo.challenger_version)
        challenger.role = PRODUCTION
        challenger.promoted_at = time.time()
        promo.status = "approved"
        promo.decided_at = time.time()
        promo.decided_by = decided_by
        _withdraw_stale_pending(db, None, exclude_id=promo.id)
        db.flush()
        check_invariants(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


def reject(db: Session, promo: PromotionRequest, decided_by: Optional[str]) -> None:
    """Reject: the request and its challenger are marked rejected, which frees the shadow slot (BUG-4)."""
    try:
        row = _row(db, promo.challenger_version)
        if row is not None and row.role != PRODUCTION:  # never demote production by rejecting a stale request
            row.role = REJECTED
        promo.status = "rejected"
        promo.decided_at = time.time()
        promo.decided_by = decided_by
        db.flush()
        _, shadow = current_roles(db)
        _withdraw_stale_pending(db, shadow, exclude_id=promo.id)
        check_invariants(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
