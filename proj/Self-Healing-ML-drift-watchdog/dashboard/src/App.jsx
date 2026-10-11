import { useEffect, useState, useCallback, useRef, Fragment } from "react";
import { api, API_URL, getAdminKey, setAdminKey } from "./api";
import { useTheme } from "./useTheme";

const POLL_MS = 5000;
let toastIdCounter = 0;

/* ─── Helpers ───────────────────────────────────── */

function formatTime(unixSeconds) {
  if (!unixSeconds) return "—";
  return new Date(unixSeconds * 1000).toLocaleTimeString();
}

function formatMoney(value) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

/* ─── Toast Notifications ────────────────────────── */

function ToastContainer({ toasts, onDismiss }) {
  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast toast-${t.type}`}
          onClick={() => onDismiss(t.id)}
        >
          <span className="toast-icon">
            {t.type === "success" ? "✓" : t.type === "error" ? "✕" : "ℹ"}
          </span>
          <span className="toast-message">{t.message}</span>
        </div>
      ))}
    </div>
  );
}

/* ─── Theme Toggle ───────────────────────────────── */

function ThemeToggle({ theme, setTheme }) {
  return (
    <div className="theme-toggle">
      <button
        className={theme === "light" ? "active" : ""}
        onClick={() => setTheme("light")}
        title="Light mode"
      >
        ☀
      </button>
      <button
        className={theme === "dark" ? "active" : ""}
        onClick={() => setTheme("dark")}
        title="Dark mode"
      >
        ☾
      </button>
    </div>
  );
}

/* ─── KPI Strip ──────────────────────────────────── */

function KPIStrip({ status, stats, latestDrift }) {
  const driftTriggered = latestDrift?.triggered;

  return (
    <div className="kpi-strip">
      {/* Card 1: Production Model */}
      <div className="kpi-card">
        <div className="kpi-content">
          <div className="kpi-label">Production Model</div>
          <div className="kpi-value">{status?.production_version ?? "—"}</div>
          <div className="kpi-sub">
            {status?.shadow_version
              ? `Shadow: ${status.shadow_version}`
              : "No shadow active"}
          </div>
        </div>
        <div
          className={`kpi-indicator ${status?.shadow_version ? "amber" : "green"}`}
        />
      </div>

      {/* Card 2: Total Predictions */}
      <div className="kpi-card">
        <div className="kpi-content">
          <div className="kpi-label">Total Predictions</div>
          <div className="kpi-value">
            {formatNumber(stats?.total_predictions)}
          </div>
          <div className="kpi-sub">
            {formatNumber(stats?.total_labeled)} labeled
          </div>
        </div>
      </div>

      {/* Card 3: Model Accuracy */}
      <div className="kpi-card">
        <div className="kpi-content">
          <div className="kpi-label">Model Accuracy (MAE)</div>
          <div className="kpi-value">
            {stats?.production_mae != null
              ? formatMoney(stats.production_mae)
              : "—"}
          </div>
          <div className="kpi-sub">
            {stats?.production_latency_ms != null
              ? `Avg error | Latency: ${stats.production_latency_ms} ms`
              : "Avg error on recent labels"}
          </div>
        </div>
        <div
          className={`kpi-indicator ${
            stats?.production_mae == null
              ? "neutral"
              : stats.production_mae < 20000
                ? "green"
                : stats.production_mae < 35000
                  ? "amber"
                  : "red"
          }`}
        />
      </div>

      {/* Card 4: Drift Status */}
      <div className={`kpi-card ${driftTriggered ? "kpi-alert" : ""}`}>
        <div className="kpi-content">
          <div className="kpi-label">Drift Status</div>
          <div className="kpi-value">
            {latestDrift == null
              ? "No checks"
              : driftTriggered
                ? "Drift Detected"
                : "Stable"}
          </div>
          <div className="kpi-sub">
            {latestDrift
              ? `Score: ${latestDrift.drift_score}`
              : "Run a drift check"}
          </div>
        </div>
        <div
          className={`kpi-indicator ${
            latestDrift == null
              ? "neutral"
              : driftTriggered
                ? "red"
                : "green"
          }`}
        />
      </div>
    </div>
  );
}

/* ─── Watchdog AI Executive Summary ─────────────── */

function WatchdogAIInsight({ latestDrift, pendingPromotion, status }) {
  if (pendingPromotion) {
    return (
      <div className="ai-executive-card promotion-focus">
        <div className="ai-card-header">
          <div className="ai-card-title">
            <span className="ai-badge-live">AI EVALUATION INSIGHT</span>
            <h3>Challenger {pendingPromotion.challenger_version} Ready for Promotion Review</h3>
          </div>
          <span className="badge pending">Action Required</span>
        </div>
        <p className="ai-summary-text">
          {pendingPromotion.explanation ||
            `Challenger ${pendingPromotion.challenger_version} has been statistically validated against ${pendingPromotion.production_version_at_request} on ${pendingPromotion.n_labels_compared} paired live requests.`}
        </p>
        <div className="ai-summary-metrics">
          <span className="metric-pill">
            MAE: <strong>{formatMoney(pendingPromotion.production_mae)} → {formatMoney(pendingPromotion.challenger_mae)}</strong>
          </span>
          {pendingPromotion.production_latency_ms != null && pendingPromotion.challenger_latency_ms != null && (
            <span className="metric-pill">
              Latency: <strong>{pendingPromotion.production_latency_ms.toFixed(1)}ms (prod) vs {pendingPromotion.challenger_latency_ms.toFixed(1)}ms (challenger)</strong>
            </span>
          )}
          <span className="metric-pill">
            Paired Samples: <strong>{pendingPromotion.n_labels_compared}</strong>
          </span>
        </div>
      </div>
    );
  }

  if (latestDrift?.triggered) {
    const driftedFeatures = latestDrift.details
      ? Object.entries(latestDrift.details)
          .filter(([_, v]) => v.drifted)
          .map(([k, v]) => ({ name: k, ...v }))
      : [];

    return (
      <div className="ai-executive-card drift-focus">
        <div className="ai-card-header">
          <div className="ai-card-title">
            <span className="ai-badge-live drift">WATCHDOG AI LIVE SUMMARY</span>
            <h3>Data Drift Detected (Score: {latestDrift.drift_score})</h3>
          </div>
          <span className="badge triggered">
            {status?.shadow_version ? `Shadow ${status.shadow_version} Active` : "Retraining"}
          </span>
        </div>
        <p className="ai-summary-text">
          {latestDrift.explanation ||
            `Statistically significant drift detected across ${driftedFeatures.length} live feature(s) compared to the baseline training distribution.`}
        </p>
        {driftedFeatures.length > 0 && (
          <div className="ai-summary-metrics">
            <span className="ai-metric-label">Drifted Features:</span>
            {driftedFeatures.map((f) => (
              <span key={f.name} className="feature-pill">
                <strong>{f.name}</strong> (KS={f.ks_statistic})
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ai-executive-card stable-focus">
      <div className="ai-card-header">
        <div className="ai-card-title">
          <span className="ai-badge-live calm">WATCHDOG AI STATUS</span>
          <h3>Pipeline Operating Normally</h3>
        </div>
        <span className="badge quiet">Distribution Stable</span>
      </div>
      <p className="ai-summary-text">
        {latestDrift
          ? `Last drift check (timesteps ${latestDrift.window_start_timestep}–${latestDrift.window_end_timestep}) confirmed live inputs closely match baseline distribution (score: ${latestDrift.drift_score}).`
          : "System is monitoring incoming live requests. No drift events detected."}
      </p>
    </div>
  );
}

/* ─── Action Bar ─────────────────────────────────── */

function ActionBar({
  onUpload,
  onTrainBaseline,
  onDriftCheck,
  onRetrain,
  loading,
  baselineReady,
  shadowVersion,
}) {
  const fileInputRef = useRef(null);

  return (
    <div className="action-bar">
      <div className="action-bar-title">Quick Actions</div>
      <div className="action-bar-buttons">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files[0]) {
              onUpload(e.target.files[0]);
              e.target.value = "";
            }
          }}
        />
        <button
          className="action-btn primary"
          onClick={() => fileInputRef.current?.click()}
          disabled={!!loading}
          id="upload-data-btn"
        >
          {loading === "upload" ? (
            <>
              <span className="spinner" /> Uploading…
            </>
          ) : (
            "Upload Data"
          )}
        </button>
        <button
          className="action-btn secondary"
          onClick={onTrainBaseline}
          disabled={!!loading || baselineReady}
          title={
            baselineReady
              ? "A baseline model already exists. Use Retrain Challenger to build a replacement."
              : "Train the first (v1) model"
          }
          id="train-baseline-btn"
        >
          {loading === "train" ? (
            <>
              <span className="spinner" /> Training…
            </>
          ) : (
            "Train Baseline"
          )}
        </button>
        <button
          className="action-btn secondary"
          onClick={onDriftCheck}
          disabled={!!loading}
          id="drift-check-btn"
        >
          {loading === "drift" ? (
            <>
              <span className="spinner" /> Checking…
            </>
          ) : (
            "Run Drift Check"
          )}
        </button>
        <button
          className="action-btn secondary"
          onClick={onRetrain}
          disabled={!!loading || !!shadowVersion}
          title={
            shadowVersion
              ? `Challenger ${shadowVersion} is already in shadow evaluation. Decide on it first.`
              : "Train a new challenger on recent live data"
          }
          id="retrain-btn"
        >
          {loading === "retrain" ? (
            <>
              <span className="spinner" /> Retraining…
            </>
          ) : (
            "Retrain Challenger"
          )}
        </button>
      </div>
    </div>
  );
}

/* ─── Promotion Requests ─────────────────────────── */

function PromotionRequests({ requests, onDecide, busyId }) {
  const pending = requests.filter((r) => r.status === "pending");
  const decided = requests.filter((r) => r.status !== "pending");

  return (
    <section id="promotions">
      <h2>Promotion Requests</h2>
      <p className="section-desc">
        Created only when the evaluation gate finds a challenger that is
        statistically proven better on real, recent traffic. Nothing here is
        applied until you approve it.
      </p>

      {pending.length === 0 && decided.length === 0 && (
        <div className="empty-state">No promotion requests yet.</div>
      )}

      {pending.map((r) => (
        <div className="promo-card" key={r.id}>
          <div className="promo-header">
            <div className="promo-title">
              <span className="promo-version">{r.challenger_version}</span>
              <span className="promo-arrow">→</span>
              <span>replace {r.production_version_at_request}</span>
            </div>
            <span className="badge pending">pending</span>
          </div>
          <div className="promo-metrics">
            <div className="metric-card">
              <div className="metric-label">Production MAE</div>
              <div className="metric-value">{formatMoney(r.production_mae)}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Challenger MAE</div>
              <div className="metric-value challenger-better">
                {formatMoney(r.challenger_mae)}
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Prod Latency</div>
              <div className="metric-value">
                {r.production_latency_ms != null ? `${r.production_latency_ms.toFixed(1)} ms` : "—"}
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Challenger Latency</div>
              <div
                className={`metric-value ${
                  r.challenger_latency_ms != null &&
                  r.production_latency_ms != null &&
                  r.challenger_latency_ms <= r.production_latency_ms
                    ? "challenger-better"
                    : ""
                }`}
              >
                {r.challenger_latency_ms != null ? `${r.challenger_latency_ms.toFixed(1)} ms` : "—"}
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Paired Samples</div>
              <div className="metric-value">{r.n_labels_compared}</div>
            </div>
          </div>
          {r.explanation && (
            <div className="ai-note">
              <span className="ai-tag">AI Summary</span>
              <p>{r.explanation}</p>
            </div>
          )}
          <div className="promo-actions">
            <button
              className="btn btn-approve"
              disabled={busyId === r.id}
              onClick={() => onDecide(r.id, "approve")}
              id={`approve-btn-${r.id}`}
            >
              ✓ Approve
            </button>
            <button
              className="btn btn-reject"
              disabled={busyId === r.id}
              onClick={() => onDecide(r.id, "reject")}
              id={`reject-btn-${r.id}`}
            >
              ✕ Reject
            </button>
          </div>
        </div>
      ))}

      {decided.length > 0 && (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Challenger</th>
                <th>Was Replacing</th>
                <th className="num">Challenger MAE</th>
                <th className="num">Prod MAE</th>
                <th className="num">Challenger Latency</th>
                <th className="num">Prod Latency</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {decided.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td className="mono">{r.challenger_version}</td>
                  <td className="mono">{r.production_version_at_request}</td>
                  <td className="num mono">{formatMoney(r.challenger_mae)}</td>
                  <td className="num mono">{formatMoney(r.production_mae)}</td>
                  <td className="num mono">
                    {r.challenger_latency_ms != null ? `${r.challenger_latency_ms.toFixed(1)} ms` : "—"}
                  </td>
                  <td className="num mono">
                    {r.production_latency_ms != null ? `${r.production_latency_ms.toFixed(1)} ms` : "—"}
                  </td>
                  <td>
                    <span className={`badge ${r.status}`}>{r.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ─── Drift Events ───────────────────────────────── */

function DriftEvents({ events }) {
  const latestTriggered = events.find((e) => e.triggered);
  const [expandedId, setExpandedId] = useState(latestTriggered ? latestTriggered.id : null);

  useEffect(() => {
    if (expandedId === null && latestTriggered) {
      setExpandedId(latestTriggered.id);
    }
  }, [latestTriggered?.id]);

  return (
    <section id="drift">
      <h2>Drift Monitor Checks</h2>
      <p className="section-desc">
        KS-test comparing recent live input features against the training
        distribution. Two or more features drifting significantly triggers an
        alert. Click a row to toggle per-feature details.
      </p>
      {events.length === 0 ? (
        <div className="empty-state">No drift checks have been run yet.</div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Window (Timestep)</th>
                <th className="num">Drift Score</th>
                <th>Result</th>
                <th>Checked At</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <Fragment key={e.id}>
                  <tr
                    className={e.triggered ? "clickable-row" : ""}
                    onClick={() =>
                      e.triggered &&
                      setExpandedId(expandedId === e.id ? null : e.id)
                    }
                  >
                    <td>
                      {e.window_start_timestep}–{e.window_end_timestep}
                    </td>
                    <td className="num mono">{e.drift_score}</td>
                    <td>
                      <span
                        className={`badge ${e.triggered ? "triggered" : "quiet"}`}
                      >
                        {e.triggered ? "drift detected" : "no drift"}
                      </span>
                      {e.triggered && (
                        <span className="expand-indicator">
                          {expandedId === e.id ? "▲ collapse" : "▼ AI details"}
                        </span>
                      )}
                    </td>
                    <td>{formatTime(e.checked_at)}</td>
                  </tr>
                  {expandedId === e.id && (
                    <tr className="detail-row">
                      <td colSpan={4}>
                        {e.details && (
                          <div className="drift-detail">
                            <div className="drift-detail-title">
                              Per-Feature KS Test Results
                            </div>
                            <div className="drift-detail-grid">
                              {Object.entries(e.details).map(([feat, v]) => (
                                <div
                                  key={feat}
                                  className={`drift-feature-card ${v.drifted ? "drifted" : ""}`}
                                >
                                  <div className="drift-feature-name">{feat}</div>
                                  <div className="drift-feature-stats">
                                    <span>
                                      KS: <strong>{v.ks_statistic}</strong>
                                    </span>
                                    <span>
                                      p: <strong>{v.p_value}</strong>
                                    </span>
                                  </div>
                                  <span
                                    className={`badge ${v.drifted ? "triggered" : "quiet"}`}
                                  >
                                    {v.drifted ? "drifted" : "stable"}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {e.explanation && (
                          <div className="ai-note">
                            <span className="ai-tag">AI Summary</span>
                            <p>{e.explanation}</p>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ─── Recent Predictions ─────────────────────────── */

function RecentPredictions({ predictions }) {
  return (
    <section id="predictions">
      <h2>Recent Predictions</h2>
      <p className="section-desc">
        Newest first. Shadow rows are scored silently and never affect what was
        actually returned to the caller.
      </p>
      {predictions.length === 0 ? (
        <div className="empty-state">No predictions logged yet.</div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Timestep</th>
                <th>Model</th>
                <th></th>
                <th className="num">Prediction</th>
                <th className="num">True Label</th>
                <th className="num">Latency</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((p) => (
                <tr key={p.id}>
                  <td>{p.timestep}</td>
                  <td className="mono">{p.model_version}</td>
                  <td>
                    {p.is_shadow && (
                      <span className="badge shadow">shadow</span>
                    )}
                  </td>
                  <td className="num mono">{formatMoney(p.prediction)}</td>
                  <td className="num mono">
                    {p.true_label !== null ? formatMoney(p.true_label) : "—"}
                  </td>
                  <td className="num mono">
                    {p.latency_ms != null ? `${p.latency_ms} ms` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ─── Admin key prompt ───────────────────────────── */

function AdminKeyPrompt({ onSave }) {
  const [value, setValue] = useState("");
  const submit = (e) => {
    e.preventDefault();
    if (value.trim()) onSave(value.trim());
  };
  return (
    <form className="admin-key-prompt" onSubmit={submit}>
      <label htmlFor="admin-key-input">Admin key</label>
      <input
        id="admin-key-input"
        type="password"
        autoComplete="off"
        placeholder="Same value as ADMIN_API_KEY on the server"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="submit" className="action-btn primary" disabled={!value.trim()}>
        Save key
      </button>
    </form>
  );
}

/* ─── Main App ───────────────────────────────────── */

export default function App() {
  const [theme, setTheme] = useTheme();
  const [status, setStatus] = useState(null);
  const [stats, setStats] = useState(null);
  const [predictions, setPredictions] = useState([]);
  const [driftEvents, setDriftEvents] = useState([]);
  const [promotionRequests, setPromotionRequests] = useState([]);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [actionLoading, setActionLoading] = useState(null);
  const [needsKey, setNeedsKey] = useState(false);

  const addToast = useCallback((message, type = "info") => {
    const id = ++toastIdCounter;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, st, p, d, r] = await Promise.all([
        api.getStatus(),
        api.getStats(),
        api.getPredictions(50),
        api.getDriftEvents(20),
        api.getPromotionRequests(),
      ]);
      setStatus(s);
      setStats(st);
      setPredictions(p);
      setDriftEvents(d);
      setPromotionRequests(r);
      setError(null);
      setNeedsKey(false);
    } catch (e) {
      if (e.status === 401) {
        setNeedsKey(true);
        setError(
          getAdminKey()
            ? "The server rejected the admin key. Enter the correct one below."
            : "This dashboard needs the admin key. Enter it below."
        );
      } else if (e.status) {
        setError(e.message); // e.g. 503: server locked because ADMIN_API_KEY isn't set
      } else {
        setError(`Could not reach the serving API at ${API_URL}. Is it running?`);
      }
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  /* Action handlers */

  const reportError = (prefix, e) => {
    if (e.status === 401) setNeedsKey(true);
    addToast(`${prefix}: ${e.message}`, e.status === 409 ? "info" : "error");
  };

  const saveKey = (key) => {
    setAdminKey(key);
    setNeedsKey(false);
    refresh();
  };

  const handleDecide = async (id, decision) => {
    setBusyId(id);
    try {
      await api.decidePromotion(id, decision);
      addToast(
        `Promotion ${decision === "approve" ? "approved" : "rejected"} successfully.`,
        decision === "approve" ? "success" : "info"
      );
      await refresh();
    } catch (e) {
      reportError("Could not record decision", e);
    } finally {
      setBusyId(null);
    }
  };

  const handleUpload = async (file) => {
    setActionLoading("upload");
    try {
      const result = await api.uploadStream(file);
      addToast(
        `Uploaded ${result.filename} — ${result.rows} rows saved. ` +
          "Run the simulator to stream them." +
          (result.backup ? ` Previous file kept as ${result.backup}.` : ""),
        "success"
      );
      await refresh();
    } catch (e) {
      reportError("Upload failed", e);
    } finally {
      setActionLoading(null);
    }
  };

  const handleTrainBaseline = async () => {
    setActionLoading("train");
    try {
      await api.trainBaseline();
      addToast(
        "Baseline training started — model will update shortly.",
        "success"
      );
      setTimeout(refresh, 3000);
    } catch (e) {
      reportError("Training not started", e);
    } finally {
      setActionLoading(null);
    }
  };

  const handleDriftCheck = async () => {
    setActionLoading("drift");
    try {
      const result = await api.runDriftCheck();
      if (result.status === "skipped") {
        addToast(result.message, "info");
      } else {
        addToast(
          result.triggered
            ? `Drift detected! Score: ${result.score}, ${result.n_drifted} features drifted.`
            : `No drift detected. Score: ${result.score}`,
          result.triggered ? "error" : "success"
        );
      }
      await refresh();
    } catch (e) {
      reportError("Drift check failed", e);
    } finally {
      setActionLoading(null);
    }
  };

  const handleRetrain = async () => {
    setActionLoading("retrain");
    try {
      await api.retrain();
      addToast(
        "Retraining started — watch for a new shadow model.",
        "success"
      );
      setTimeout(refresh, 3000);
    } catch (e) {
      reportError("Retrain not started", e);
    } finally {
      setActionLoading(null);
    }
  };

  const latestDrift = driftEvents.length > 0 ? driftEvents[0] : null;
  const pendingPromotion =
    promotionRequests.find((r) => r.status === "pending") || null;

  return (
    <>
      <div className="topbar">
        <div>
          <h1>
            Drift-Watchdog
            <span className="header-ai-badge">AI-Assisted</span>
          </h1>
          <div className="subtitle">
            Self-healing ML pipeline — monitor, review, approve
          </div>
        </div>
        <ThemeToggle theme={theme} setTheme={setTheme} />
      </div>

      <ToastContainer toasts={toasts} onDismiss={removeToast} />

      {error && <div className="error-banner">{error}</div>}
      {needsKey && <AdminKeyPrompt onSave={saveKey} />}

      <KPIStrip status={status} stats={stats} latestDrift={latestDrift} />
      <WatchdogAIInsight
        latestDrift={latestDrift}
        pendingPromotion={pendingPromotion}
        status={status}
      />
      <ActionBar
        onUpload={handleUpload}
        onTrainBaseline={handleTrainBaseline}
        onDriftCheck={handleDriftCheck}
        onRetrain={handleRetrain}
        loading={actionLoading}
        baselineReady={!!status?.baseline_ready}
        shadowVersion={status?.shadow_version}
      />
      <PromotionRequests
        requests={promotionRequests}
        onDecide={handleDecide}
        busyId={busyId}
      />
      <DriftEvents events={driftEvents} />
      <RecentPredictions predictions={predictions} />
    </>
  );
}
