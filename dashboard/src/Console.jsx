import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useDashboard } from "./hooks/useDashboard";
import { deriveHealth } from "./lib/health";
import { formatClock, formatInt, formatMoney, formatMs, formatPercent, improvement } from "./lib/format";
import { Header } from "./components/Header";
import { Section } from "./components/ui";
import { KpiStrip } from "./components/KpiStrip";
import { StatusCard } from "./components/StatusCard";
import { ActionsCard } from "./components/ActionsCard";
import { AutomationCard } from "./components/AutomationCard";
import { DriftMonitor } from "./components/DriftMonitor";
import { PromotionRequests } from "./components/PromotionRequests";
import { RecentPredictions } from "./components/RecentPredictions";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { Toasts } from "./components/Toasts";

const BASELINE_WAIT_MS = 5 * 60 * 1000; // give up watching a baseline job after this long
let toastId = 0;

/** Confirmation copy per risky action. Approve shows the evidence it is based on. */
function ConfirmBody({ confirm }) {
  const r = confirm?.request;
  switch (confirm?.kind) {
    case "approve":
      return (
        <>
          <p>
            Approving makes <strong>{r.challenger_version}</strong> the production model. Live traffic switches to it immediately.
          </p>
          <dl className="dl">
            <dt>Production {r.production_version_at_request} error (MAE)</dt><dd>{formatMoney(r.production_mae)}</dd>
            <dt>Challenger {r.challenger_version} error (MAE)</dt><dd>{formatMoney(r.challenger_mae)}</dd>
            <dt>Error reduction</dt><dd>{formatPercent(improvement(r.production_mae, r.challenger_mae))}</dd>
            <dt>Paired samples</dt><dd>{formatInt(r.n_labels_compared)}</dd>
            <dt>Inference latency (prod / challenger)</dt><dd>{formatMs(r.production_latency_ms)} / {formatMs(r.challenger_latency_ms)}</dd>
          </dl>
        </>
      );
    case "reject":
      return (
        <p>
          Reject <strong>{r.challenger_version}</strong>? It is removed from shadow and marked rejected, and cannot be put back. You can then train a new challenger.
        </p>
      );
    case "retrain":
      return <p>Train a new challenger on the most recent labelled live predictions and test it in shadow. Production is not affected; nothing is promoted without your approval.</p>;
    case "train":
      return <p>Create the first model (v1) from the training data. It takes about a minute and never overwrites an existing baseline.</p>;
    default:
      return null;
  }
}

const CONFIRM_TEXT = {
  approve: { title: "Approve and promote?", label: "Approve and promote" },
  reject: { title: "Reject this challenger?", label: "Reject challenger", variant: "danger" },
  retrain: { title: "Retrain a challenger?", label: "Start retraining" },
  train: { title: "Train the baseline model?", label: "Train baseline" },
};

const READ_ONLY_REASON = "Read-only access: sign in with an admin account to make changes.";

/** The signed-in dashboard. Mounted only after login, so nothing is fetched before that. */
export default function Console({ user, onSignOut, theme, setTheme }) {
  const readOnly = user.role !== "admin";
  const { data, conn, refresh, boost } = useDashboard(() => onSignOut("Your session has ended. Please sign in again."));
  const [toasts, setToasts] = useState([]);
  const [busy, setBusy] = useState(null); // one in-flight request: upload | drift | retrain | train | auto:*
  const [decisionId, setDecisionId] = useState(null);
  const [trainingBaseline, setTrainingBaseline] = useState(false);
  const [confirm, setConfirm] = useState(null); // { kind, request? }
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const health = deriveHealth({ conn, driftEvents: data.driftEvents });
  const stale = conn.state === "offline";

  useEffect(() => {
    const tag = { drift: "Drift detected", offline: "Offline", waiting: "Waiting for data", locked: "Admin key needed" }[health.kind];
    document.title = `${tag ? `${tag} · ` : ""}Drift-Watchdog`;
  }, [health.kind]);

  const toast = useCallback((message, type = "info") => {
    const id = ++toastId;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 6000);
  }, []);
  const dismissToast = useCallback((id) => setToasts((prev) => prev.filter((t) => t.id !== id)), []);

  const fail = (prefix, e) => toast(`${prefix}: ${e.message}`, e.status === 409 ? "info" : "error");

  /** Run one action: mark it in flight, report the outcome, then refresh straight away. */
  const act = async (name, run, onError) => {
    setBusy(name);
    try {
      await run();
    } catch (e) {
      (onError || fail)(e);
    } finally {
      setBusy(null);
      refresh();
    }
  };

  /* ---- actions ---- */
  const doUpload = (file) =>
    act("upload", async () => {
      const r = await api.uploadStream(file);
      toast(`Uploaded ${r.filename}: ${formatInt(r.rows)} rows saved. Run the simulator to stream them.${r.backup ? ` Previous file kept as ${r.backup}.` : ""}`, "success");
    }, (e) => fail("Upload failed", e));

  const doDriftCheck = () =>
    act("drift", async () => {
      const r = await api.runDriftCheck();
      if (r.status === "skipped") toast(r.message, "info");
      else toast(r.triggered ? `Drift detected (score ${r.score}, ${r.n_drifted} features).` : `No drift (score ${r.score}).`, r.triggered ? "error" : "success");
    }, (e) => fail("Drift check failed", e));

  const doRetrain = () =>
    act("retrain", async () => {
      await api.retrain();
      boost();
      toast("Retraining started. A new shadow model appears when it finishes.", "success");
    }, (e) => fail("Retrain not started", e));

  /** The spinner follows /status.baseline_ready, not the request being accepted. */
  const doTrainBaseline = async () => {
    setBusy("train");
    try {
      await api.trainBaseline();
    } catch (e) {
      fail("Training not started", e);
      setBusy(null);
      refresh();
      return;
    }
    setBusy(null);
    setTrainingBaseline(true);
    boost(BASELINE_WAIT_MS);
    const deadline = Date.now() + BASELINE_WAIT_MS;
    let ready = false;
    while (alive.current && Date.now() < deadline && !ready) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        ready = !!(await api.getStatus()).baseline_ready;
      } catch {
        /* transient: keep waiting */
      }
    }
    if (alive.current) {
      setTrainingBaseline(false);
      toast(ready ? "Baseline model v1 is ready." : "Baseline training did not finish in 5 minutes. Check the server log.", ready ? "success" : "error");
      refresh();
    }
  };

  const doDecide = async (request, decision) => {
    setDecisionId(request.id);
    try {
      await api.decidePromotion(request.id, decision);
      toast(
        decision === "approve" ? `${request.challenger_version} is now the production model.` : `${request.challenger_version} was rejected.`,
        decision === "approve" ? "success" : "info"
      );
      setConfirm(null);
    } catch (e) {
      fail("Could not record decision", e);
      setConfirm(null);
    } finally {
      setDecisionId(null);
      refresh();
    }
  };

  const doAutomation = (name, call, message) =>
    act(`auto:${name}`, async () => {
      await call();
      toast(message, "success");
    }, (e) => fail("Automation", e));

  const onConfirm = () => {
    const { kind, request } = confirm;
    if (kind === "approve" || kind === "reject") return doDecide(request, kind);
    setConfirm(null);
    return kind === "retrain" ? doRetrain() : doTrainBaseline();
  };

  const latestDrift = data.driftEvents?.[0] ?? null;
  const unavailable = conn.state === "offline" || conn.state === "locked";
  const confirmText = confirm ? CONFIRM_TEXT[confirm.kind] : null;

  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <Header conn={conn} theme={theme} setTheme={setTheme} user={user} onSignOut={() => onSignOut()} />
      <Toasts toasts={toasts} onDismiss={dismissToast} />

      <main className="page" id="main">
        {conn.state === "offline" && (
          <div className="banner" role="alert">
            {conn.message} Showing the last data received{conn.lastOkAt ? ` at ${formatClock(conn.lastOkAt)}` : ""}, dimmed because it may be out of date.
          </div>
        )}
        {conn.state === "degraded" && <div className="banner" data-tone="warning" role="alert">{conn.message}</div>}
        {readOnly && (
          <div className="banner" data-tone="info" role="status">
            <strong>{user.guest ? "Guest mode" : "Read-only account"}:</strong> you can look at everything, but changes (retraining,
            approving, automation) are turned off. Promotions always need an admin's approval.
          </div>
        )}

        <Section id="overview" title="Overview" description="Where the pipeline stands right now.">
          <div data-stale={stale}>
            <KpiStrip status={data.status} stats={data.stats} latestDrift={latestDrift} driftLoaded={data.driftEvents != null} unavailable={unavailable} />
          </div>
          <div className="grid-overview">
            <StatusCard health={health} status={data.status} promotions={data.promotions} />
            <div data-stale={stale}>
              <ActionsCard
                status={data.status}
                connState={conn.state}
                readOnlyReason={readOnly ? READ_ONLY_REASON : null}
                busy={busy}
                trainingBaseline={trainingBaseline}
                onUpload={doUpload}
                onDriftCheck={doDriftCheck}
                onTrainBaseline={() => setConfirm({ kind: "train" })}
                onRetrain={() => setConfirm({ kind: "retrain" })}
              />
            </div>
          </div>
        </Section>

        <div data-stale={stale}>
          <AutomationCard
            automation={data.automation}
            connState={conn.state}
            readOnlyReason={readOnly ? READ_ONLY_REASON : null}
            busy={busy?.startsWith("auto:") ? busy.slice(5) : null}
            onStart={(seconds) => doAutomation("start", () => api.startAutomation(seconds), "Automation started. It never promotes anything on its own.")}
            onStop={() => doAutomation("stop", () => api.stopAutomation(), "Automation stopped.")}
            onRunNow={() => doAutomation("run", () => api.runAutomationNow(), "Cycle started. The result appears in the card when it finishes.")}
          />
          <DriftMonitor events={data.driftEvents} />
          <PromotionRequests
            requests={data.promotions}
            busyId={decisionId}
            connState={conn.state}
            readOnlyReason={readOnly ? READ_ONLY_REASON : null}
            onDecide={(request, kind) => setConfirm({ kind, request })}
          />
          <RecentPredictions predictions={data.predictions} />
        </div>
      </main>

      <ConfirmDialog
        open={!!confirm}
        title={confirmText?.title ?? ""}
        confirmLabel={confirmText?.label ?? ""}
        confirmVariant={confirmText?.variant}
        busy={decisionId != null}
        onConfirm={onConfirm}
        onCancel={() => setConfirm(null)}
      >
        <ConfirmBody confirm={confirm} />
      </ConfirmDialog>
    </>
  );
}
