import { Fragment, useState } from "react";
import { formatScore } from "../lib/format";
import { Badge, EmptyState, RelativeTime, Section, Skeleton, Summary, TableCaption } from "./ui";
import { Sparkline } from "./Sparkline";

/** Timesteps are optional, so a window is described by the request ids it covers (and timesteps when present). */
function windowLabel(e) {
  const ids = e.window_end_log_id != null ? `#${e.window_start_log_id}–#${e.window_end_log_id}` : null;
  const steps = e.window_end_timestep != null ? `t ${e.window_start_timestep}–${e.window_end_timestep}` : null;
  return [ids, steps].filter(Boolean).join(" · ") || "—";
}

function SkeletonRows({ cols }) {
  return Array.from({ length: 3 }, (_, r) => (
    <tr key={r}>{Array.from({ length: cols }, (_, c) => <td key={c}><Skeleton width="70%" /></td>)}</tr>
  ));
}

export function DriftMonitor({ events }) {
  // undefined = the user hasn't touched it: show the newest drift event expanded.
  // Derived during render rather than set from an effect.
  const [choice, setChoice] = useState(undefined);
  const loading = events == null;
  const newestDrift = events?.find((e) => e.triggered);
  const expandedId = choice !== undefined ? choice : newestDrift?.id ?? null;

  return (
    <Section
      id="drift"
      title="Drift monitor"
      description="Each check runs a KS test per monitored feature, live requests against the training data. Two or more features drifting significantly triggers an alert."
    >
      {loading ? (
        <div className="table-wrap" aria-busy="true"><table><tbody><SkeletonRows cols={5} /></tbody></table></div>
      ) : events.length === 0 ? (
        <EmptyState title="No drift checks yet">
          Stream some traffic (<code>python simulator/stream_simulator.py</code>), then press <em>Run drift check</em> or start Automation.
        </EmptyState>
      ) : (
        <>
          {events.length > 1 && (
            <div className="trend">
              <Sparkline events={events} />
              <p className="trend-legend">
                Highest per-feature KS statistic over the last {events.length} checks. Dashed line: the per-feature threshold; red ticks: checks that raised an alert.
              </p>
            </div>
          )}
          <div className="table-wrap" style={{ maxHeight: 560 }} tabIndex={0} aria-label="Drift checks, scrollable">
            <table>
              <TableCaption>Drift monitor checks, newest first</TableCaption>
              <thead>
                <tr>
                  <th scope="col">Checked</th>
                  <th scope="col">Window</th>
                  <th scope="col">Compared with</th>
                  <th scope="col" className="num">Score</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => {
                  const open = expandedId === e.id;
                  return (
                    <Fragment key={e.id}>
                      <tr>
                        <td><RelativeTime ts={e.checked_at} /></td>
                        <td className="mono" title="The newest requests the check looked at">{windowLabel(e)}</td>
                        <td className="mono" title="Drift is measured against the data this model version was trained on">
                          {e.reference_version ? `${e.reference_version}'s data` : "—"}
                        </td>
                        <td className="num mono">{formatScore(e.drift_score)}</td>
                        <td>
                          <Badge tone={e.triggered ? "danger" : "success"}>{e.triggered ? "Drift detected" : "No drift"}</Badge>
                          {e.details && (
                            <button
                              type="button" className="link-btn" aria-expanded={open} aria-controls={`drift-detail-${e.id}`}
                              onClick={() => setChoice(open ? null : e.id)}
                            >
                              {open ? "Hide details" : "Details"}
                            </button>
                          )}
                        </td>
                      </tr>
                      {open && e.details && (
                        <tr className="detail-row" id={`drift-detail-${e.id}`}>
                          <td colSpan={5}>
                            <div className="feature-grid">
                              {Object.entries(e.details).map(([name, f]) => (
                                <div key={name} className={`feature ${f.drifted ? "drifted" : ""}`}>
                                  <div className="feature-name">{name}</div>
                                  <div className="feature-stats"><span>KS <strong>{f.ks_statistic}</strong></span><span>p <strong>{f.p_value}</strong></span></div>
                                  <Badge tone={f.drifted ? "danger" : "success"}>{f.drifted ? "Drifted" : "Stable"}</Badge>
                                </div>
                              ))}
                            </div>
                            {e.explanation && <Summary>{e.explanation}</Summary>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  );
}
