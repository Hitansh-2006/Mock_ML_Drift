import { formatInt, formatLatency, formatMoney, formatPercent, improvement } from "../lib/format";
import { Badge, Button, EmptyState, RelativeTime, Section, Skeleton, Summary, TableCaption } from "./ui";
import { CompareBar } from "./CompareBar";

const STATUS_TONE = { pending: "warning", approved: "success", rejected: "danger", withdrawn: "neutral" };

function PendingCard({ r, busyId, blocked, onDecide }) {
  const gain = improvement(r.production_mae, r.challenger_mae);
  const working = busyId === r.id;
  return (
    <article className="card promo" aria-label={`Promotion request ${r.id}: ${r.challenger_version} replacing ${r.production_version_at_request}`}>
      <div className="promo-head">
        <div className="promo-title">
          {r.challenger_version}<span className="arrow" aria-hidden="true">→</span>replace {r.production_version_at_request}
        </div>
        <Badge tone="warning">Pending your decision</Badge>
      </div>

      <CompareBar
        productionMae={r.production_mae} challengerMae={r.challenger_mae}
        productionVersion={r.production_version_at_request} challengerVersion={r.challenger_version}
      />

      <div className="metrics">
        <div className="metric"><div className="metric-label">Paired samples</div><div className="metric-value">{formatInt(r.n_labels_compared)}</div></div>
        <div className="metric"><div className="metric-label">Error reduction</div><div className={`metric-value ${gain > 0 ? "good" : ""}`}>{formatPercent(gain)}</div></div>
        <div className="metric"><div className="metric-label">Prod inference p50 / p95</div><div className="metric-value metric-small">{formatLatency(r.production_latency_ms, r.production_latency_p95_ms)}</div></div>
        <div className="metric"><div className="metric-label">Challenger inference p50 / p95</div><div className="metric-value metric-small">{formatLatency(r.challenger_latency_ms, r.challenger_latency_p95_ms)}</div></div>
      </div>

      {r.explanation && <Summary>{r.explanation}</Summary>}

      <div className="promo-actions">
        <Button variant="primary" loading={working} loadingLabel="Working…" disabledReason={blocked || (busyId != null && !working ? "Another decision is in progress." : undefined)} onClick={() => onDecide(r, "approve")} aria-label={`Approve ${r.challenger_version}`}>
          Approve…
        </Button>
        <Button variant="danger" disabledReason={blocked || (busyId != null ? "A decision is in progress." : undefined)} onClick={() => onDecide(r, "reject")} aria-label={`Reject ${r.challenger_version}`}>
          Reject…
        </Button>
      </div>
    </article>
  );
}

export function PromotionRequests({ requests, busyId, connState, readOnlyReason, onDecide }) {
  const loading = requests == null;
  const pending = (requests || []).filter((r) => r.status === "pending");
  const decided = (requests || []).filter((r) => r.status !== "pending");
  const blocked = readOnlyReason || (connState === "offline" ? "The serving API is not reachable." : connState === "locked" ? "Your session has ended. Sign in again." : undefined);

  return (
    <Section
      id="promotions"
      title="Promotion requests"
      description="Created only when the evaluation gate finds a challenger that is statistically better on real, recent traffic. Nothing is applied until you approve it."
    >
      {loading ? (
        <div className="card" aria-busy="true"><Skeleton width="40%" height={22} /><Skeleton height={12} style={{ marginTop: 16 }} /><Skeleton height={12} style={{ marginTop: 8 }} /></div>
      ) : (
        <>
          {pending.length === 0 && (
            <EmptyState title={decided.length ? "Nothing waiting for your decision" : "No promotion requests yet"}>
              A request appears here once a challenger in shadow passes the evaluation gate — start Automation, or use <em>Retrain challenger</em> and keep streaming labelled traffic.
            </EmptyState>
          )}
          {pending.map((r) => <PendingCard key={r.id} r={r} busyId={busyId} blocked={blocked} onDecide={onDecide} />)}

          {decided.length > 0 && (
            <div className="table-wrap" tabIndex={0} aria-label="Past promotion requests, scrollable" style={{ marginTop: 16 }}>
              <table>
                <TableCaption>Past promotion requests</TableCaption>
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Created</th>
                    <th scope="col">Challenger</th>
                    <th scope="col">Replaced</th>
                    <th scope="col" className="num">Challenger MAE</th>
                    <th scope="col" className="num">Production MAE</th>
                    <th scope="col" className="num">Samples</th>
                    <th scope="col">Status</th>
                    <th scope="col">Decided by</th>
                  </tr>
                </thead>
                <tbody>
                  {decided.map((r) => (
                    <tr key={r.id}>
                      <td>{r.id}</td>
                      <td><RelativeTime ts={r.created_at} /></td>
                      <td className="mono">{r.challenger_version}</td>
                      <td className="mono">{r.production_version_at_request}</td>
                      <td className="num mono">{formatMoney(r.challenger_mae)}</td>
                      <td className="num mono">{formatMoney(r.production_mae)}</td>
                      <td className="num mono">{formatInt(r.n_labels_compared)}</td>
                      <td><Badge tone={STATUS_TONE[r.status] || "neutral"}>{r.status}</Badge></td>
                      <td>
                        {r.decided_by ? <><span className="mono">{r.decided_by}</span> <span className="muted">· <RelativeTime ts={r.decided_at} /></span></> : <span className="muted">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
