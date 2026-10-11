import { useEffect, useState } from "react";

const INTERVALS = [
  { seconds: 15, label: "15 s" },
  { seconds: 30, label: "30 s" },
  { seconds: 60, label: "1 min" },
  { seconds: 300, label: "5 min" },
  { seconds: 900, label: "15 min" },
];

function formatCountdown(seconds) {
  if (seconds <= 0) return "any moment";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function formatInterval(seconds) {
  const known = INTERVALS.find((i) => i.seconds === seconds);
  if (known) return known.label;
  return seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`;
}

/** Plain-words summary of the last cycle (the server writes `message`; this is the fallback). */
function describeCycle(cycle) {
  if (!cycle) return "No cycle has run yet.";
  if (cycle.message) return cycle.message;
  if (cycle.error) return `Error: ${cycle.error}`;
  if (cycle.drift_triggered) return "Drift detected";
  return cycle.drift_checked ? "No drift" : "Waiting for traffic";
}

export function AutomationCard({ automation, state, busy, onStart, onStop, onRunNow }) {
  // `state`: "ok" | "offline" | "locked" (401). Controls are disabled unless "ok".
  const [now, setNow] = useState(() => Date.now());
  const [interval, setIntervalSeconds] = useState(60);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const running = !!automation?.running;
  const usable = state === "ok" && automation;
  const nextIn = running && automation.next_run_at ? automation.next_run_at - now / 1000 : null;
  const last = automation?.last_cycle;
  const lastWasError = !!last?.error;

  let pill = { cls: "neutral", text: "Stopped" };
  if (state === "offline") pill = { cls: "offline", text: "Unavailable" };
  else if (state === "locked") pill = { cls: "offline", text: "Locked" };
  else if (running) pill = { cls: "running", text: "Running" };

  return (
    <section id="automation" aria-labelledby="automation-title">
      <div className="automation-card">
        <div className="automation-head">
          <div>
            <h2 id="automation-title">Automation</h2>
            <p className="section-desc">
              Checks for drift, trains a challenger and tests it on live traffic.{" "}
              <strong>Promotions always need your approval.</strong>
            </p>
          </div>
          <span className={`status-pill ${pill.cls}`} role="status" aria-label={`Automation is ${pill.text}`}>
            <span className="status-dot" aria-hidden="true" />
            {pill.text}
          </span>
        </div>

        <dl className="automation-facts">
          <div>
            <dt>Next run</dt>
            <dd>
              {!usable
                ? "—"
                : running
                  ? automation.cycle_in_progress
                    ? "running now…"
                    : nextIn != null
                      ? `in ${formatCountdown(nextIn)}`
                      : "—"
                  : "not scheduled"}
            </dd>
          </div>
          <div>
            <dt>Interval</dt>
            <dd>{usable ? `every ${formatInterval(automation.interval_seconds)}` : "—"}</dd>
          </div>
          <div className="automation-last">
            <dt>Last cycle</dt>
            <dd className={lastWasError ? "text-danger" : ""}>
              {!usable ? "—" : describeCycle(last)}
              {usable && automation.last_cycle_at && (
                <span className="muted"> · {new Date(automation.last_cycle_at * 1000).toLocaleTimeString()}</span>
              )}
            </dd>
          </div>
        </dl>

        <div className="automation-actions">
          {running ? (
            <button
              type="button"
              className="action-btn secondary"
              onClick={onStop}
              disabled={!usable || busy === "stop"}
              aria-label="Stop the automation loop"
            >
              {busy === "stop" ? <><span className="spinner" /> Stopping…</> : "Stop automation"}
            </button>
          ) : (
            <>
              <label className="automation-interval">
                <span>Check every</span>
                <select
                  value={interval}
                  onChange={(e) => setIntervalSeconds(Number(e.target.value))}
                  disabled={!usable || busy === "start"}
                >
                  {INTERVALS.map((i) => (
                    <option key={i.seconds} value={i.seconds}>{i.label}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="action-btn primary"
                onClick={() => onStart(interval)}
                disabled={!usable || busy === "start"}
                aria-label="Start the automation loop"
              >
                {busy === "start" ? <><span className="spinner" /> Starting…</> : "Start automation"}
              </button>
            </>
          )}
          <button
            type="button"
            className="action-btn secondary"
            onClick={onRunNow}
            disabled={!usable || busy === "run" || automation?.cycle_in_progress}
            title="Run one drift → retrain → gate cycle right now"
          >
            {busy === "run" ? <><span className="spinner" /> Starting…</> : "Run once now"}
          </button>
        </div>

        {state === "offline" && <p className="automation-note">The serving API is not reachable, so the loop state is unknown.</p>}
        {state === "locked" && <p className="automation-note">Enter the admin key above to see and control the loop.</p>}
      </div>
    </section>
  );
}
