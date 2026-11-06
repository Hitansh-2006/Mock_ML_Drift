"""
The serving service. Responsibilities:
  1. Load the current production model (and, if one is running, a shadow
     challenger model) at startup.
  2. Serve predictions on the current production model only.
  3. Log every prediction (features, prediction, model version, timestep)
     to the DB — this log is what the drift monitor and evaluation gate
     read from.
  4. Accept true labels later (delayed-label problem) via /label.
  5. Never auto-promote anything — /admin/promote is a separate,
     human-triggered concern (added in the evaluation-gate step).

Run with: uvicorn serving.app:app --reload --port 8000
"""
import json
import os
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from fastapi import APIRouter, FastAPI, Depends, Header, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from automation.controller import AutomationController, MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS
from automation.loop import CycleDeps
from serving import auth, metrics, registry
from serving.db import init_db, get_session, SessionLocal, PredictionLog, PromotionRequest, DriftEvent
from data.house_price_preprocessor import HousePricePreprocessor
from serving.security import require_admin, auth_mode, current_principal, guest_enabled, is_configured

ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "models"
DATA_DIR = ROOT_DIR / "data"
TRAINING_PATH = DATA_DIR / "training_data.csv"

# Uploaded stream files larger than this are rejected (the house-price stream is ~0.2 MB).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# One baseline-training job and one retrain job at a time (single serving process).
TRAIN_LOCK = threading.Lock()
RETRAIN_LOCK = threading.Lock()
# Serialises registry transitions (set_shadow / approve / reject) so "write the DB,
# then update the cache" is atomic with respect to other requests in this process.
REGISTRY_LOCK = threading.RLock()


@asynccontextmanager
async def lifespan(_app):
    startup()
    AUTOMATION.restore()      # switch the loop back on if it was on before the restart
    yield
    AUTOMATION.shutdown()     # stop the scheduler; `enabled` stays set so the next boot restores it


app = FastAPI(title="Drift-Watchdog Serving Service", lifespan=lifespan)

# Only the dashboard's origin(s) may call this API from a browser. Defaults to
# the local dev server (5173) and the Docker Compose dashboard (also 5173).
# Deploying elsewhere? Set DASHBOARD_ORIGINS="https://dash.example.com,...".
DASHBOARD_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "DASHBOARD_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# --- in-memory cache of the DB model registry ---------------------------------
# The model_registry table is the source of truth for which version is
# production / shadow; startup() fills these two keys from it and every change
# writes the DB first. The cache only exists so /predict doesn't hit the DB for
# the version on every request. Single serving process only (one uvicorn worker).
#
# Each version needs a MODEL + a PREPROCESSOR loaded together: a raw
# feature dict (e.g. {"OverallQual": 7, "Neighborhood": "CollgCr", ...})
# has to go through that version's fitted preprocessor before the model can
# score it. They're always saved and loaded as a pair.
STATE = {
    "production_version": "v1",
    "shadow_version": None,
    "models": {},  # version -> {"model": ..., "preprocessor": ...}
}


def load_model(version: str):
    if version not in STATE["models"]:
        model_path = MODELS_DIR / f"model_{version}.joblib"
        preproc_path = MODELS_DIR / f"preprocessor_{version}.joblib"
        if not model_path.exists():
            raise FileNotFoundError(f"No model file for version {version} at {model_path}")
        if not preproc_path.exists():
            raise FileNotFoundError(f"No preprocessor file for version {version} at {preproc_path}")
        STATE["models"][version] = {
            "model": joblib.load(model_path),
            "preprocessor": HousePricePreprocessor.load(preproc_path),
            "calls": 0,  # requests scored since it was loaded; the first few are warm-up (see _score)
        }
    return STATE["models"][version]


# The first requests after a model is loaded are slower (lazy initialisation, cold caches). They are
# scored normally but record NO latency, so they can't skew the p50/p95 numbers (BUG-8).
WARMUP_CALLS = int(os.environ.get("LATENCY_WARMUP_CALLS", "3"))


def _score(entry, features):
    """
    Predict with an ALREADY LOADED model. Returns (dollar prediction, inference latency in ms),
    with None for the latency while the model is still warming up. The timer covers
    preprocessing + inference only: never the disk load, never HTTP.
    """
    t0 = time.perf_counter()
    X = entry["preprocessor"].transform_single(features)
    pred_log = entry["model"].predict(X)[0]
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    entry["calls"] += 1
    warming_up = entry["calls"] <= WARMUP_CALLS
    # the model predicts log1p(SalePrice); invert for a real dollar figure
    return float(np.expm1(pred_log)), (None if warming_up else latency_ms)


class TransitionError(Exception):
    """A refused registry transition; the HTTP layer turns it into an HTTPException."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _sync_cache(db: Session):
    """Copy the DB's production/shadow roles into STATE. Call only after the DB write committed."""
    prod, shadow = registry.current_roles(db)
    STATE["production_version"] = prod or STATE["production_version"]
    STATE["shadow_version"] = shadow


def apply_set_shadow(version: str, db: Session) -> dict:
    """
    Deploys `version` to the shadow slot: files must exist and load, then the
    registry is updated (old shadow archived) and only then the cache. Used by
    POST /admin/set_shadow and directly (no HTTP) by the in-process automation loop.
    """
    with REGISTRY_LOCK:
        if not (MODELS_DIR / f"model_{version}.joblib").exists() \
                or not (MODELS_DIR / f"preprocessor_{version}.joblib").exists():
            raise TransitionError(404, f"Missing model or preprocessor file for version {version}")
        _load_or_raise(version)
        try:
            registry.set_shadow(db, version, registry.describe(MODELS_DIR, version))
        except registry.RegistryError as e:
            raise TransitionError(e.status_code, e.detail)
        _sync_cache(db)
        return {"status": "ok", "shadow_version": version}


def _load_or_raise(version: str):
    try:
        return load_model(version)
    except Exception as e:  # missing file, corrupt joblib, incompatible pickle...
        raise TransitionError(500, f"Model {version} could not be loaded: {e}")


def startup():
    """Boot logic: create tables, seed the registry on a legacy DB, load STATE from it."""
    init_db()
    db = SessionLocal()
    try:
        if registry.seed_if_empty(db, MODELS_DIR):
            print(f"[startup] model_registry was empty - seeded production={registry.current_roles(db)[0]} "
                  f"from existing promotion requests.")
        _sync_cache(db)
    finally:
        db.close()
    try:
        load_model(STATE["production_version"])
    except FileNotFoundError as e:
        print(f"[startup] WARNING: {e}. /predict will return 503 until a baseline model "
              f"exists (dashboard: Train Baseline, or `python -m models.train_baseline`).")
    db = SessionLocal()
    try:
        seeded = auth.seed_admin_from_env(db)
        if seeded:
            print(f"[startup] {seeded}")
        mode = auth_mode(db)
    finally:
        db.close()
    if mode == "locked":
        print("[startup] WARNING: no account and no ADMIN_API_KEY - the /admin API is LOCKED (503). "
              "Set ADMIN_USERNAME + ADMIN_PASSWORD in .env (or run `python -m serving.manage_users create`), "
              "or ADMIN_AUTH_DISABLED=1 for local development only.")
    elif mode == "disabled":
        print("[startup] WARNING: ADMIN_AUTH_DISABLED=1 - /admin endpoints are UNPROTECTED. Local dev only.")


# --- schemas -------------------------------------------------------------------
class PredictRequest(BaseModel):
    features: dict  # e.g. {"feature_0": 0.12, "feature_1": -1.3, ...}
    timestep: Optional[int] = None


class PredictResponse(BaseModel):
    prediction: float
    model_version: str
    log_id: int
    latency_ms: Optional[float] = None  # inference time only (no HTTP, no disk load); null while warming up
    request_id: Optional[str] = None    # ties this response to its shadow twin and to its label


class LabelRequest(BaseModel):
    log_id: int
    true_label: float


# --- endpoints -------------------------------------------------------------------
@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest, db: Session = Depends(get_session)):
    prod_version = STATE["production_version"]
    try:
        prod = load_model(prod_version)
    except FileNotFoundError:
        raise HTTPException(
            503, "No production model is available yet. Train the baseline first "
                 "(dashboard: Train Baseline, or `python -m models.train_baseline`)."
        )

    request_id = uuid.uuid4().hex  # shared by the production row and its shadow twin: this is the pairing key
    pred, prod_latency_ms = _score(prod, req.features)

    log = PredictionLog(
        timestep=req.timestep,
        request_id=request_id,
        features=req.features,
        prediction=pred,
        model_version=prod_version,
        is_shadow=False,
        latency_ms=prod_latency_ms,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # if a shadow challenger is active, score it too (logged, never returned
    # to the caller, never affects the served prediction)
    shadow_version = STATE["shadow_version"]  # read once: it can change mid-request
    if shadow_version:
        try:
            shadow = load_model(shadow_version)  # load FIRST: a cold disk read must not count as inference time
            shadow_pred, shadow_latency_ms = _score(shadow, req.features)

            shadow_log = PredictionLog(
                timestep=req.timestep,
                request_id=request_id,
                features=req.features,
                prediction=shadow_pred,
                model_version=shadow_version,
                is_shadow=True,
                latency_ms=shadow_latency_ms,
            )
            db.add(shadow_log)
            db.commit()
        except Exception as e:
            # Shadow scoring is log-only: a missing/corrupt shadow must never fail the caller's request.
            db.rollback()
            print(f"[predict] shadow {shadow_version} skipped: {e}")

    return PredictResponse(
        prediction=pred,
        model_version=prod_version,
        log_id=log.id,
        latency_ms=prod_latency_ms,
        request_id=request_id,
    )


@app.post("/label")
def submit_label(req: LabelRequest, db: Session = Depends(get_session)):
    """
    Attaches a true label to a previously-logged prediction, once it's
    known (this is the delayed-label problem — e.g. actual delivery time
    is only known after the delivery happens). Also back-fills the shadow
    prediction(s) made for the SAME request: they are found by request_id, which
    is unique per /predict call, so a label can never leak onto another request
    or another shadow version (the old timestep match could, and real traffic
    has no timesteps at all). Legacy rows without a request_id get no back-fill.
    """
    log = db.query(PredictionLog).filter(PredictionLog.id == req.log_id).first()
    if not log:
        raise HTTPException(404, "log_id not found")
    log.true_label = req.true_label

    if log.request_id and not log.is_shadow:
        (
            db.query(PredictionLog)
            .filter(PredictionLog.request_id == log.request_id, PredictionLog.is_shadow == True)  # noqa: E712
            .update({"true_label": req.true_label}, synchronize_session=False)
        )

    db.commit()
    return {"status": "ok", "log_id": req.log_id}


@app.get("/status")
def status():
    return {
        "production_version": STATE["production_version"],
        "shadow_version": STATE["shadow_version"],
        "baseline_ready": _baseline_exists(),
    }


def _baseline_exists() -> bool:
    """True only if all three baseline files exist (train() writes the metadata file last)."""
    return all(
        (MODELS_DIR / name).exists()
        for name in ("model_v1.joblib", "preprocessor_v1.joblib", "model_v1.meta.json")
    )


# --- sign-in (public by design: it is how you obtain a session) ------------------
auth_router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class SignupRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    role: Optional[str] = "admin"


def _require_configured(db: Session):
    if not is_configured(db):
        raise HTTPException(503, "Sign-in is not set up: no account exists. "
                                 "Set ADMIN_USERNAME and ADMIN_PASSWORD (see .env.example) and restart.")


@auth_router.get("/config")
def auth_config(db: Session = Depends(get_session)):
    """What the login page needs to know before anyone has signed in."""
    return {"configured": is_configured(db), "guest_enabled": guest_enabled(), "signup_enabled": True}


@auth_router.post("/signup")
def signup(req: SignupRequest, request: Request, db: Session = Depends(get_session)):
    client = request.client.host if request.client else "unknown"
    wait = auth.retry_after(req.username, client)
    if wait:
        raise HTTPException(429, f"Too many failed attempts. Try again in {wait} seconds.",
                            headers={"Retry-After": str(wait)})
    role = req.role if req.role in auth.ROLES else "admin"
    try:
        user = auth.create_user(db, req.username, req.password, role)
    except ValueError as e:
        auth.record_failure(req.username, client)
        raise HTTPException(400, str(e))
    auth.record_success(req.username, client)
    return auth.create_session(db, user.username, user.role)


@auth_router.post("/login")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_session)):
    _require_configured(db)
    client = request.client.host if request.client else "unknown"
    wait = auth.retry_after(req.username, client)
    if wait:
        raise HTTPException(429, f"Too many failed attempts. Try again in {wait} seconds.",
                            headers={"Retry-After": str(wait)})
    user = auth.authenticate(db, req.username, req.password)
    if user is None:
        auth.record_failure(req.username, client)
        raise HTTPException(401, "Invalid username or password.")  # same answer for unknown user and wrong password
    auth.record_success(req.username, client)
    return auth.create_session(db, user.username, user.role)


@auth_router.post("/guest")
def guest_login(db: Session = Depends(get_session)):
    """A read-only session with no account. Disable with GUEST_LOGIN_ENABLED=0."""
    if not guest_enabled():
        raise HTTPException(403, "Guest access is turned off on this server.")
    _require_configured(db)
    return auth.create_session(db, "guest", "viewer", guest=True)


@auth_router.post("/logout")
def logout(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_session)):
    if authorization and authorization[:7].lower() == "bearer ":
        auth.revoke_session(db, authorization[7:].strip())
    return {"status": "ok"}


@auth_router.get("/me")
def me(x_api_key: Optional[str] = Header(default=None), authorization: Optional[str] = Header(default=None),
       db: Session = Depends(get_session)):
    principal = current_principal(db, x_api_key, authorization)
    if principal is None:
        raise HTTPException(401, "Not signed in.", headers={"WWW-Authenticate": "Bearer"})
    return {"username": principal.username, "role": principal.role, "guest": principal.guest}


# Every /admin/* route below hangs off this router, so the credential check and the
# read-only rule for viewers/guests are applied in exactly one place (security.require_admin)
# and can't be forgotten on a new endpoint.
admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@admin.get("/predictions")
def list_predictions(limit: int = 50, db: Session = Depends(get_session)):
    """Most recent predictions (production + shadow), newest first."""
    rows = db.query(PredictionLog).order_by(PredictionLog.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "request_id": r.request_id,
            "timestep": r.timestep,
            "model_version": r.model_version,
            "is_shadow": r.is_shadow,
            "prediction": r.prediction,
            "true_label": r.true_label,
            "latency_ms": r.latency_ms,
            "served_at": r.served_at,
        }
        for r in rows
    ]


@admin.get("/drift_events")
def list_drift_events(limit: int = 20, db: Session = Depends(get_session)):
    """Most recent drift-monitor checks, newest first."""
    rows = db.query(DriftEvent).order_by(DriftEvent.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "checked_at": r.checked_at,
            "window_start_timestep": r.window_start_timestep,
            "window_end_timestep": r.window_end_timestep,
            "window_start_log_id": r.window_start_log_id,
            "window_end_log_id": r.window_end_log_id,
            "reference_version": r.reference_version,
            "drift_score": r.drift_score,
            "method": r.method,
            "threshold": r.threshold,
            "triggered": r.triggered,
            "details": r.details,
            "explanation": r.explanation,
        }
        for r in rows
    ]


class SetShadowRequest(BaseModel):
    version: str


@admin.post("/set_shadow")
def set_shadow(req: SetShadowRequest, db: Session = Depends(get_session)):
    """
    Points the shadow slot at a new challenger model version (the previous
    shadow, if any, is archived). Deliberately has NO equivalent for
    production — promoting to production only ever happens through a
    separate, explicitly human-approved path (/admin/promote).
    """
    try:
        return apply_set_shadow(req.version, db)
    except TransitionError as e:
        raise HTTPException(e.status_code, e.detail)


class PromotionDecisionRequest(BaseModel):
    promotion_request_id: int
    decision: str  # "approve" or "reject" — nothing else is accepted
    decided_by: Optional[str] = None  # ignored: the server records the signed-in user instead


@admin.get("/promotion_requests")
def list_promotion_requests(db: Session = Depends(get_session)):
    """Lists pending promotion requests for a human to review."""
    rows = db.query(PromotionRequest).order_by(PromotionRequest.id.desc()).all()
    return [
        {
            "id": r.id,
            "challenger_version": r.challenger_version,
            "production_version_at_request": r.production_version_at_request,
            "challenger_mae": r.challenger_mae,
            "production_mae": r.production_mae,
            "challenger_latency_ms": r.challenger_latency_ms,        # inference p50
            "production_latency_ms": r.production_latency_ms,
            "challenger_latency_p95_ms": r.challenger_latency_p95_ms,
            "production_latency_p95_ms": r.production_latency_p95_ms,
            "n_labels_compared": r.n_labels_compared,
            "status": r.status,
            "created_at": r.created_at,
            "decided_at": r.decided_at,
            "decided_by": r.decided_by,
            "explanation": r.explanation,
        }
        for r in rows
    ]


@admin.post("/promote")
def decide_promotion(req: PromotionDecisionRequest, db: Session = Depends(get_session),
                     principal: auth.Principal = Depends(require_admin)):
    """
    THE ONLY code path in this entire system that can change which model
    serves production traffic. Requires an explicit human decision on an
    existing promotion request created by the evaluation gate — there is no
    way to promote a model that hasn't already passed the statistical gate,
    and no automatic path that calls this endpoint on its own.
    """
    if req.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")

    # The audit trail names the authenticated caller. `req.decided_by` is accepted for old
    # clients but ignored: a client must not be able to choose who is recorded as the approver.
    decided_by = principal.username
    with REGISTRY_LOCK:
        promo = db.query(PromotionRequest).filter(PromotionRequest.id == req.promotion_request_id).first()
        if not promo:
            raise HTTPException(404, "promotion request not found")
        if promo.status != "pending":
            raise HTTPException(400, f"promotion request is already '{promo.status}'")

        try:
            if req.decision == "approve":
                # Validate BEFORE touching the DB: an unloadable challenger must
                # leave the request pending and production untouched (BUG-7).
                registry.assert_promotable(db, promo)
                _load_or_raise(promo.challenger_version)
                registry.promote(db, promo, decided_by)
            else:
                registry.reject(db, promo, decided_by)
        except registry.RegistryError as e:
            raise HTTPException(e.status_code, e.detail)
        except TransitionError as e:
            raise HTTPException(e.status_code, e.detail)
        _sync_cache(db)  # DB committed; now the cache follows

        return {
            "status": "ok",
            "promotion_status": promo.status,
            "production_version": STATE["production_version"],
            "shadow_version": STATE["shadow_version"],
        }


# --- aggregate stats (KPI strip) -----------------------------------------------
@admin.get("/stats")
def get_stats(db: Session = Depends(get_session)):
    """Aggregate numbers for the dashboard KPI strip."""
    total_predictions = db.query(PredictionLog).filter(
        PredictionLog.is_shadow == False  # noqa: E712
    ).count()
    total_labeled = db.query(PredictionLog).filter(
        PredictionLog.is_shadow == False,  # noqa: E712
        PredictionLog.true_label.isnot(None),
    ).count()
    shadow_predictions = db.query(PredictionLog).filter(
        PredictionLog.is_shadow == True  # noqa: E712
    ).count()

    # Everything below describes the model in production NOW. Mixing in rows served by its
    # predecessors would average two different models into one meaningless number (BUG-9).
    production_version = STATE["production_version"]
    served_by_production = [
        PredictionLog.is_shadow == False,  # noqa: E712
        PredictionLog.model_version == production_version,
    ]
    recent_labeled = (
        db.query(PredictionLog)
        .filter(*served_by_production, PredictionLog.true_label.isnot(None))
        .order_by(PredictionLog.id.desc())
        .limit(200)
        .all()
    )
    recent_prod = (
        db.query(PredictionLog)
        .filter(*served_by_production)
        .order_by(PredictionLog.id.desc())
        .limit(100)
        .all()
    )
    latency_p50, latency_p95 = metrics.latency_percentiles([r.latency_ms for r in recent_prod])

    production_mae = None
    if recent_labeled:
        errors = [abs(r.prediction - r.true_label) for r in recent_labeled]
        production_mae = sum(errors) / len(errors)

    return {
        "total_predictions": total_predictions,
        "total_labeled": total_labeled,
        "production_version": production_version,
        "production_mae": production_mae,                 # null until this version has labelled rows
        "production_mae_n": len(recent_labeled),
        # How to read the MAE without house-price constants: relative to what this model scored on
        # its own validation split, with the warn/alert ratios coming from config.
        "expected_mae": registry.describe(MODELS_DIR, production_version).get("metrics", {}).get("mae"),
        "mae_warn_ratio": metrics.ratio_setting("MAE_WARN_RATIO", 1.3),
        "mae_alert_ratio": metrics.ratio_setting("MAE_ALERT_RATIO", 2.0),
        "production_latency_ms": latency_p50,             # inference p50 over the last 100 requests
        "production_latency_p95_ms": latency_p95,
        "shadow_predictions": shadow_predictions,
    }


# --- action endpoints (dashboard buttons) --------------------------------------
@admin.post("/upload_stream")
def upload_stream(file: UploadFile):
    """
    Replaces data/stream_data.csv with an uploaded CSV - safely:
      1. the file is read with a size cap and written to a temp file first,
      2. it must parse as a CSV and contain every column of training_data.csv
         plus `timestep` (`drifted` is optional; extra columns are allowed),
         with a unique integer `timestep` and a numeric, non-empty `SalePrice`,
      3. only then is the old file backed up (stream_data.bak.csv) and the new
         one swapped in atomically.
    A bad upload is rejected with a 400 and leaves the existing file untouched.
    This only replaces the file on disk: run simulator/stream_simulator.py to
    actually stream it to the API.
    """
    import pandas as pd

    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    if not contents.strip():
        raise HTTPException(400, "The uploaded file is empty.")

    dest = DATA_DIR / "stream_data.csv"
    backup = DATA_DIR / "stream_data.bak.csv"
    tmp = DATA_DIR / f".stream_upload_{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(contents)

        try:
            df = pd.read_csv(tmp)
        except Exception as e:  # unparsable / binary / wrong encoding
            raise HTTPException(400, f"Could not read the file as a CSV: {str(e)[:200]}")
        if df.empty:
            raise HTTPException(400, "The CSV has a header but no data rows.")

        expected = list(pd.read_csv(TRAINING_PATH, nrows=0).columns) + ["timestep"]
        missing = [c for c in expected if c not in df.columns]
        if missing:
            shown = ", ".join(missing[:8]) + (f" ... (+{len(missing) - 8} more)" if len(missing) > 8 else "")
            raise HTTPException(
                400, f"The CSV is missing {len(missing)} required column(s): {shown}. "
                     f"It needs every column of training_data.csv plus 'timestep'.")

        ts = pd.to_numeric(df["timestep"], errors="coerce")
        if ts.isna().any() or (ts % 1 != 0).any():
            raise HTTPException(400, "'timestep' must contain whole numbers with no blanks.")
        if ts.duplicated().any():
            raise HTTPException(400, "'timestep' values must be unique (they pair production and shadow predictions).")
        target = pd.to_numeric(df["SalePrice"], errors="coerce")
        if target.isna().any():
            raise HTTPException(400, "'SalePrice' must be numeric with no blanks (it is the label sent back after each prediction).")

        had_backup = False
        if dest.exists():
            shutil.copy2(dest, backup)
            had_backup = True
        os.replace(tmp, dest)  # atomic: readers see the old file or the new one, never a half-written one
    finally:
        tmp.unlink(missing_ok=True)

    extra = [c for c in df.columns if c not in expected and c != "drifted"]
    return {
        "status": "ok",
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "filename": file.filename,
        "backup": backup.name if had_backup else None,
        "extra_columns": extra,
    }


@admin.post("/train_baseline")
def train_baseline_endpoint(background_tasks: BackgroundTasks):
    """
    Creates the FIRST model (v1) in the background. It never overwrites an
    existing baseline: once v1 exists, replacing what serves production only
    happens through retrain -> shadow -> evaluation gate -> human approval.
    """
    if _baseline_exists():
        raise HTTPException(
            409, "A baseline model (v1) already exists, and this button never overwrites it. "
                 "Use 'Retrain Challenger' to build a replacement - it goes through shadow "
                 "testing and human approval.")
    if not TRAIN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Baseline training is already running.")

    def _train():
        try:
            from models.train_baseline import train
            train("v1")
            STATE["models"].pop("v1", None)
            load_model("v1")
        except Exception as e:
            print(f"[train_baseline] failed: {e}")
        finally:
            TRAIN_LOCK.release()

    background_tasks.add_task(_train)
    return {"status": "started", "message": "Baseline training started in background. It can take about a minute."}


@admin.post("/run_drift_check")
def run_drift_check_endpoint():
    """Runs the drift monitor once and returns the result."""
    from monitor.drift_monitor import run_once
    result = run_once(window_size=200)
    if result is None:
        return {"status": "skipped", "message": "Not enough data or no new predictions since last check."}
    return {
        "status": "ok",
        "triggered": result["triggered"],
        "score": result["overall_score"],
        "n_drifted": result["n_drifted_features"],
    }


@admin.post("/retrain")
def retrain_endpoint(background_tasks: BackgroundTasks):
    """
    Triggers challenger retraining + shadow deployment in the background.
    Follows the same rule as the automation loop: no new retrain while a
    challenger is already in shadow, and never two retrains at once.
    """
    shadow = STATE["shadow_version"]
    if shadow:
        raise HTTPException(
            409, f"Challenger {shadow} is already in shadow evaluation. Let the evaluation gate "
                 f"resolve it (and approve/reject it) before training another.")
    if not RETRAIN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "A retrain is already running.")

    def _retrain():
        try:
            from retrain.retrain_pipeline import run_retrain
            run_retrain(n_recent=500)
        except Exception as e:
            print(f"[retrain] failed: {e}")
        finally:
            RETRAIN_LOCK.release()

    background_tasks.add_task(_retrain)
    return {"status": "started", "message": "Retrain started in background. Watch /status for the new shadow version."}


# --- automation loop (dashboard Start/Stop button) -----------------------------
def _deploy_shadow_directly(version: str) -> dict:
    """What the in-process loop uses instead of POSTing to its own /admin/set_shadow."""
    db = SessionLocal()
    try:
        return apply_set_shadow(version, db)
    except TransitionError as e:
        raise RuntimeError(f"set_shadow refused ({e.status_code}): {e.detail}")
    finally:
        db.close()


def _automation_deps() -> CycleDeps:
    # RETRAIN_LOCK is the same lock /admin/retrain takes: one retrain at a time, button or loop.
    return CycleDeps(get_status=status, deploy_shadow=_deploy_shadow_directly, retrain_lock=RETRAIN_LOCK)


AUTOMATION = AutomationController(deps_factory=_automation_deps)


class AutomationStartRequest(BaseModel):
    interval_seconds: Optional[int] = Field(default=None, ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS)


@admin.get("/automation")
def automation_status():
    """Is the loop on, when does it next run, and what did the last cycle do?"""
    return AUTOMATION.status()


@admin.post("/automation/start")
def automation_start(req: Optional[AutomationStartRequest] = None):
    """Starts the loop (idempotent). The loop only ever creates *pending* promotion requests."""
    return AUTOMATION.start(req.interval_seconds if req else None)


@admin.post("/automation/stop")
def automation_stop():
    """Stops the loop (idempotent). A cycle that is already running finishes."""
    return AUTOMATION.stop()


@admin.post("/automation/run_now")
def automation_run_now():
    """Runs one cycle immediately in the background (handy for demos)."""
    if not AUTOMATION.run_now():
        raise HTTPException(409, "An automation cycle is already running.")
    return {"status": "started"}


# Registered last so every route above is attached to the router first.
app.include_router(admin)
app.include_router(auth_router)
