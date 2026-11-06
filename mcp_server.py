"""
Drift-Watchdog MCP (Model Context Protocol) Server.

Connects AI tools (such as Claude Desktop, Cursor, or Antigravity) to the
Drift-Watchdog MLOps pipeline. Exposes tools, resources, and prompt templates
to monitor data drift, trigger shadow retraining, evaluate models, and manage
human-in-the-loop promotions.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
from mcp.server.mcpserver import MCPServer

# -----------------------------------------------------------------------------
# Configuration & Environment
# -----------------------------------------------------------------------------
ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(ENV_PATH)

API_URL = os.environ.get("DRIFT_API_URL", "http://localhost:8000").rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "").strip()
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# Placeholder keys from .env.example that should not be used as real keys
_PLACEHOLDER_KEYS = {"", "change-me", "changeme", "your_admin_api_key_here"}

_session_token: Optional[str] = None
_login_failed_at: float = 0.0


# -----------------------------------------------------------------------------
# HTTP Client Helper
# -----------------------------------------------------------------------------
def _get_headers(requires_auth: bool = True) -> Dict[str, str]:
    global _session_token, _login_failed_at
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if not requires_auth:
        return headers

    if ADMIN_API_KEY and ADMIN_API_KEY.lower() not in _PLACEHOLDER_KEYS:
        headers["X-API-Key"] = ADMIN_API_KEY
        return headers

    if _session_token:
        headers["Authorization"] = f"Bearer {_session_token}"
        return headers

    # Don't spin-retry login if it recently failed
    import time
    now = time.time()
    if now - _login_failed_at < 30:
        return headers

    # Attempt automatic login if credentials exist
    if ADMIN_USERNAME and ADMIN_PASSWORD:
        try:
            resp = requests.post(
                f"{API_URL}/auth/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                timeout=(2, 5),
            )
            if resp.status_code == 200:
                _session_token = resp.json().get("token")
                if _session_token:
                    headers["Authorization"] = f"Bearer {_session_token}"
                    return headers
            else:
                _login_failed_at = now
        except Exception:
            _login_failed_at = now

    return headers


def _request(method: str, path: str, json_data: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_URL}{path}"
    requires_auth = path.startswith("/admin")
    headers = _get_headers(requires_auth=requires_auth)
    try:
        resp = requests.request(method, url, headers=headers, json=json_data, timeout=(2, 10))
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return {
            "error": "Cannot connect to Drift-Watchdog API.",
            "details": f"Ensure the backend is running at {API_URL} (e.g. via `python -m uvicorn serving.app:app --reload --port 8000`).",
        }
    except Exception as e:
        return {"error": f"Request failed: {str(e)}"}

    if resp.status_code == 401:
        # Token might have expired; retry once after clearing
        global _session_token
        _session_token = None
        headers = _get_headers()
        try:
            resp = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        except Exception:
            pass

    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# -----------------------------------------------------------------------------
# MCP Server Definition
# -----------------------------------------------------------------------------
mcp = MCPServer(name="drift-watchdog", version="1.0.0")


# -----------------------------------------------------------------------------
# MCP Tools
# -----------------------------------------------------------------------------
@mcp.tool()
def get_system_status() -> Dict[str, Any]:
    """
    Get the live operational status of Drift-Watchdog.
    Returns:
      - Current production model version
      - Current shadow challenger version (if one is deployed)
      - Model load status & reference snapshot status
      - High-level drift status
    """
    res = _request("GET", "/status")
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def get_model_metrics() -> Dict[str, Any]:
    """
    Get detailed error and latency metrics for the active production model.
    Returns:
      - Live MAE on scored requests
      - Expected validation MAE from training
      - Warning and alert ratio thresholds
      - Inference latency (p50 and p95 in ms)
    """
    res = _request("GET", "/admin/stats")
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def check_drift() -> Dict[str, Any]:
    """
    Trigger an on-demand Kolmogorov-Smirnov (KS) statistical data drift check.
    Compares recent live inference traffic features against the active
    production model's baseline training snapshot.
    Returns drifted status, drifted features, and p-values.
    """
    res = _request("POST", "/admin/run_drift_check")
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def get_drift_events(limit: int = 10) -> List[Any]:
    """
    Retrieve historical data drift events recorded by the Drift Monitor.
    Parameters:
      - limit: Maximum number of events to return (default: 10).
    """
    res = _request("GET", f"/admin/drift_events?limit={limit}")
    return res if isinstance(res, list) else [res]


@mcp.tool()
def trigger_retraining() -> Dict[str, Any]:
    """
    Trigger the automated retraining pipeline.
    Trains a challenger model on recent labeled live data, evaluates
    validation performance, and deploys it to the shadow slot for live paired testing.
    Note: Does NOT replace production (human approval is required).
    """
    res = _request("POST", "/admin/retrain")
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def get_promotion_requests() -> List[Any]:
    """
    Retrieve all model promotion requests (pending, approved, rejected, withdrawn).
    Includes the Wilcoxon signed-rank test statistical evidence comparing
    paired errors between the production model and the shadow challenger.
    """
    res = _request("GET", "/admin/promotion_requests")
    return res if isinstance(res, list) else [res]


@mcp.tool()
def decide_promotion(promotion_request_id: int, decision: str) -> Dict[str, Any]:
    """
    Approve or reject promoting a shadow challenger model to production.
    Parameters:
      - promotion_request_id: ID of the pending promotion request.
      - decision: Must be either 'approve' or 'reject'.
    Approving promotes the challenger to production and archives the old model.
    Rejecting frees the shadow slot so a new challenger can be trained.
    """
    decision_clean = decision.strip().lower()
    if decision_clean not in ("approve", "reject"):
        return {"error": "decision must be either 'approve' or 'reject'"}
    res = _request(
        "POST",
        "/admin/promote",
        {"promotion_request_id": promotion_request_id, "decision": decision_clean},
    )
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def predict(features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run an inference request on the serving model for a single property.
    Parameters:
      - features: Dictionary of feature key-values (e.g. OverallQual, GrLivArea, YearBuilt).
    Returns predicted price, inference latency in ms, request_id, and model_version.
    """
    res = _request("POST", "/predict", features)
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def get_recent_predictions(limit: int = 20) -> List[Any]:
    """
    Retrieve recent prediction logs across production and shadow models.
    Parameters:
      - limit: Maximum number of recent predictions to fetch (default: 20).
    """
    res = _request("GET", f"/admin/predictions?limit={limit}")
    return res if isinstance(res, list) else [res]


@mcp.tool()
def get_automation_status() -> Dict[str, Any]:
    """
    Get the status of the background watchdog automation loop.
    Returns running status, check interval seconds, next run time, and last cycle summary.
    """
    res = _request("GET", "/admin/automation")
    return res if isinstance(res, dict) else {"data": res}


@mcp.tool()
def control_automation(action: str, interval_seconds: int = 60) -> Dict[str, Any]:
    """
    Control the background watchdog automation scheduler.
    Parameters:
      - action: One of 'start', 'stop', or 'run_now'.
      - interval_seconds: Check interval in seconds when starting (default: 60).
    """
    action_clean = action.strip().lower()
    if action_clean == "start":
        res = _request("POST", "/admin/automation/start", {"interval_seconds": interval_seconds})
    elif action_clean == "stop":
        res = _request("POST", "/admin/automation/stop")
    elif action_clean == "run_now":
        res = _request("POST", "/admin/automation/run_now")
    else:
        return {"error": "action must be 'start', 'stop', or 'run_now'"}
    return res if isinstance(res, dict) else {"data": res}


# -----------------------------------------------------------------------------
# MCP Resources
# -----------------------------------------------------------------------------
@mcp.resource("drift://status")
def resource_status() -> str:
    """Current live operational status of Drift-Watchdog as JSON."""
    return json.dumps(_request("GET", "/status"), indent=2)


@mcp.resource("drift://stats")
def resource_stats() -> str:
    """Current live model accuracy and latency metrics as JSON."""
    return json.dumps(_request("GET", "/admin/stats"), indent=2)


@mcp.resource("drift://promotions/pending")
def resource_pending_promotions() -> str:
    """All pending promotion requests awaiting human decision as JSON."""
    requests_list = _request("GET", "/admin/promotion_requests")
    if isinstance(requests_list, list):
        pending = [r for r in requests_list if r.get("status") == "pending"]
        return json.dumps(pending, indent=2)
    return json.dumps(requests_list, indent=2)


# -----------------------------------------------------------------------------
# MCP Prompts
# -----------------------------------------------------------------------------
@mcp.prompt()
def audit_model_drift() -> str:
    """
    Prompt template to guide the AI assistant in auditing data drift,
    evaluating feature distribution shifts, and recommending next actions.
    """
    return (
        "You are an expert MLOps engineer acting as the Drift-Watchdog assistant.\n"
        "1. Call `get_system_status` and `get_model_metrics` to inspect the current production model.\n"
        "2. Call `check_drift` and `get_drift_events` to analyze recent feature distribution shifts.\n"
        "3. Provide an executive summary of: which features shifted, statistical p-values, "
        "and whether a shadow challenger retrain should be triggered.\n"
        "Remember: never promote a model without explicit human approval."
    )


@mcp.prompt()
def review_promotion_candidate() -> str:
    """
    Prompt template to guide the AI assistant in evaluating a shadow model
    candidate against production on paired real-world requests.
    """
    return (
        "You are an expert MLOps engineer reviewing a model candidate.\n"
        "1. Call `get_promotion_requests` to see pending challenger promotion requests.\n"
        "2. Analyze the paired Wilcoxon test results: error reduction, statistical significance, and latency impact.\n"
        "3. Synthesize your findings into a clear recommendation: APPROVE or REJECT, and provide the rationale.\n"
        "Ask the human operator for their decision before calling `decide_promotion`."
    )


# -----------------------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
