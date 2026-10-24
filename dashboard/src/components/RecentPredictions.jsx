import { formatMoney, formatMs } from "../lib/format";
import { Badge, EmptyState, RelativeTime, Section, Skeleton, TableCaption } from "./ui";

export function RecentPredictions({ predictions }) {
  const loading = predictions == null;
  return (
    <Section
      id="predictions"
      title="Recent predictions"
      description="Newest first. Shadow rows are scored silently and never affect what was returned to the caller."
    >
      {loading ? (
        <div className="table-wrap" aria-busy="true">
          <table><tbody>{Array.from({ length: 5 }, (_, r) => <tr key={r}>{Array.from({ length: 6 }, (_, c) => <td key={c}><Skeleton width="65%" /></td>)}</tr>)}</tbody></table>
        </div>
      ) : predictions.length === 0 ? (
        <EmptyState title="No predictions logged yet">
          Send traffic to <code>POST /predict</code> — for the demo, run <code>python simulator/stream_simulator.py --speed 0.02</code>.
        </EmptyState>
      ) : (
        <div className="table-wrap" tabIndex={0} aria-label="Recent predictions, scrollable">
          <table>
            <TableCaption>Recent predictions, newest first</TableCaption>
            <thead>
              <tr>
                <th scope="col">Served</th>
                <th scope="col">Timestep</th>
                <th scope="col">Model</th>
                <th scope="col" className="num">Prediction</th>
                <th scope="col" className="num">True label</th>
                <th scope="col" className="num">Abs. error</th>
                <th scope="col" className="num">Inference</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((p) => (
                <tr key={p.id}>
                  <td><RelativeTime ts={p.served_at} /></td>
                  <td className="mono">{p.timestep ?? "—"}</td>
                  <td>
                    <span className="mono">{p.model_version}</span>{" "}
                    {p.is_shadow && <Badge tone="accent">shadow</Badge>}
                  </td>
                  <td className="num mono">{formatMoney(p.prediction)}</td>
                  <td className="num mono">{p.true_label != null ? formatMoney(p.true_label) : <span className="muted">pending</span>}</td>
                  <td className="num mono">{p.true_label != null ? formatMoney(Math.abs(p.prediction - p.true_label)) : "—"}</td>
                  <td className="num mono" title={p.latency_ms == null ? "Warm-up request: not timed, so it can't skew the latency numbers" : "Inference time only (no network, no model loading)"}>
                    {formatMs(p.latency_ms)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
