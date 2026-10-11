import { formatScore } from "../lib/format";

const W = 300;
const H = 72;
const PAD = 6;

/**
 * Drift score over time as a tiny inline SVG (no chart library).
 * `events` are newest-first like the API returns them; red ticks mark checks
 * that triggered, the dashed line is the alert threshold.
 */
export function Sparkline({ events }) {
  const points = [...events].reverse();
  if (points.length < 2) return null;

  const threshold = points.find((p) => p.threshold != null)?.threshold ?? null;
  const max = Math.max(...points.map((p) => p.drift_score), (threshold ?? 0) * 1.4, 0.01);
  const x = (i) => PAD + (i / (points.length - 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - (v / max) * (H - 2 * PAD);

  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.drift_score).toFixed(1)}`).join(" ");
  const area = `${PAD},${H - PAD} ${line} ${W - PAD},${H - PAD}`;
  const latest = points[points.length - 1];
  const label =
    `Drift score over the last ${points.length} checks. Latest ${formatScore(latest.drift_score)}` +
    (threshold != null ? `, alert threshold ${threshold}.` : ".");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <title>{label}</title>
      {threshold != null && (
        <line
          x1={PAD} x2={W - PAD} y1={y(threshold)} y2={y(threshold)}
          stroke="var(--warning)" strokeWidth="1.5" strokeDasharray="4 4" vectorEffect="non-scaling-stroke"
        />
      )}
      <polygon points={area} fill="var(--accent)" opacity="0.12" />
      <polyline
        points={line} fill="none" stroke="var(--accent)" strokeWidth="2"
        strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke"
      />
      {points.map((p, i) =>
        p.triggered ? (
          <line
            key={p.id} x1={x(i)} x2={x(i)} y1={y(p.drift_score)} y2={H - PAD}
            stroke="var(--danger-solid)" strokeWidth="2" vectorEffect="non-scaling-stroke"
          />
        ) : null
      )}
    </svg>
  );
}
