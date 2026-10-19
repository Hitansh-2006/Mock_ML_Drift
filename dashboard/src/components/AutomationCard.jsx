import { useState } from "react";
import { useNow } from "../hooks/useNow";
import { formatCountdown, formatInterval } from "../lib/format";
import { Button, Pill, RelativeTime, Section, Skeleton } from "./ui";

const INTERVALS = [
  { seconds: 15, label: "15 s" },
  { seconds: 30, label: "30 s" },
  { seconds: 60, label: "1 min" },
  { seconds: 300, label: "5 min" },
  { seconds: 900, label: "15 min" },
];

/** Plain-words summary of a cycle. The server writes `message`; this covers older/partial results. */
function describeCycle(cycle) {
  if (!cycle) return "No cycle has run yet.";
  if (cycle.message) return cycle.message;
  if (cycle.error) return `Error: ${cycle.error}`;
  if (cycle.drift_triggered) return "Drift detected";
  return cycle.drift_checked ? "No drift" : "Waiting for traffic";
}

/**
 * Start/Stop for the in-process automation loop.
 * `connState`: online | offline | locked | degraded | loading. Controls only work when
 * we actually have the loop's state from the server.
 */
export function AutomationCard({ automation, connState, readOnlyReason, busy, onStart, onStop, onRunNow }) {
  const now = useNow(1);
  const [interval, setIntervalSeconds] = useState(60);

  const loading = automation == null && connState === "loading";
  const usable = !!automation && (connState === "online" || connState === "degraded");
  const running = !!automation?.running;
  const nextIn = running && automation.next_run_at ? automation.next_run_at - now / 1000 : null;
  const last = automation?.last_cycle;

  let pill = { tone: "neutral", text: "Stopped" };
  if (!usable && !loading) pill = { tone: "neutral", text: connState === "locked" ? "Locked" : "Unavailable" };
  else if (running) pill = { tone: "success", text: "Running" };

  const blocked =
    readOnlyReason ||
    (connState === "offline" ? "The serving API is not reachable."
      : connState === "locked" ? "Your session has ended. Sign in again."
        : !usable ? "Loading…" : undefined);

  return (
    <Section id="automation" title="Automation" description="Checks for drift, trains a challenger and tests it silently on live traffic.">
      <div className="card">
        <div className="automation-head">
          <div>
            <div className="card-title">Loop</div>
            <p><strong>Promotions always need your approval.</strong> The loop can only create a request for you to review.</p>
          </div>
          <Pill tone={pill.tone} aria-label={`Automation is ${pill.text}`}>{pill.text}</Pill>
        </div>

        <dl className="facts">
          <div>
            <dt>Next run</dt>
            <dd>
              {loading ? <Skeleton width={80} /> : !usable ? "—"
                : !running ? "not scheduled"
                  : automation.cycle_in_progress ? "running now…"
                    : nextIn != null ? `in ${formatCountdown(nextIn)}` : "—"}
            </dd>
          </div>
          <div>
            <dt>Interval</dt>
            <dd>{loading ? <Skeleton width={70} /> : usable ? `every ${formatInterval(automation.interval_seconds)}` : "—"}</dd>
          </div>
          <div>
            <dt>Last cycle</dt>
            <dd className={last?.error ? "text-danger" : ""}>
              {loading ? <Skeleton width="70%" /> : !usable ? "—" : describeCycle(last)}
              {usable && automation.last_cycle_at && <span className="muted"> · <RelativeTime ts={automation.last_cycle_at} /></span>}
            </dd>
          </div>
        </dl>

        <div className="row-actions">
          {running ? (
            <Button loading={busy === "stop"} loadingLabel="Stopping…" disabledReason={blocked} onClick={onStop} aria-label="Stop the automation loop">
              Stop automation
            </Button>
          ) : (
            <>
              <label className="field">
                <span>Check every</span>
                <select value={interval} onChange={(e) => setIntervalSeconds(Number(e.target.value))} disabled={!usable || busy === "start"}>
                  {INTERVALS.map((i) => <option key={i.seconds} value={i.seconds}>{i.label}</option>)}
                </select>
              </label>
              <Button variant="primary" loading={busy === "start"} loadingLabel="Starting…" disabledReason={blocked} onClick={() => onStart(interval)} aria-label="Start the automation loop">
                Start automation
              </Button>
            </>
          )}
          <Button
            loading={busy === "run"} loadingLabel="Starting…"
            disabledReason={blocked || (automation?.cycle_in_progress ? "A cycle is already running." : undefined)}
            onClick={onRunNow}
          >
            Run once now
          </Button>
        </div>
        {connState === "offline" && <p className="note">The serving API is not reachable, so the loop's state is unknown.</p>}
      </div>
    </Section>
  );
}
