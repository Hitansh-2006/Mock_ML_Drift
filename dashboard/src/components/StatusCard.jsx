import { HEALTH_TONE } from "../lib/health";
import { formatScore } from "../lib/format";
import { Pill, RelativeTime, Skeleton, Summary } from "./ui";

/** STATUS: one of Offline / Waiting for data / Normal / Drift detected (plus Locked and the initial load). */
export function StatusCard({ health, status, promotions }) {
  if (health.kind === "loading") {
    return (
      <div className="card status-card" aria-busy="true" data-kind="loading">
        <div className="card-title">Status</div>
        <Skeleton width="45%" height={26} />
        <Skeleton width="80%" height={16} />
        <Skeleton width="60%" height={16} />
      </div>
    );
  }

  const latest = health.latest;
  const drifted = Object.entries(latest?.details || {}).filter(([, f]) => f.drifted);
  const pending = (promotions || []).find((r) => r.status === "pending");
  const live = health.kind === "normal" || health.kind === "drift" || health.kind === "waiting";

  return (
    <div className="card status-card" data-kind={health.kind} role="status" aria-live="polite">
      <div className="status-top">
        <div className="card-title">Status</div>
        <Pill tone={HEALTH_TONE[health.kind]}>{health.label}</Pill>
      </div>
      <div className="status-headline">{health.headline}</div>
      <p className="status-detail">{health.detail}</p>

      {latest && (
        <div className="status-meta">
          <span>Last check <RelativeTime ts={latest.checked_at} /></span>
          <span aria-hidden="true">·</span>
          <span>score <strong className="mono">{formatScore(latest.drift_score)}</strong></span>
        </div>
      )}

      {health.kind === "drift" && drifted.length > 0 && (
        <div className="status-meta" aria-label="Drifted features">
          {drifted.map(([name, f]) => (
            <span key={name} className="chip drifted">{name} · KS {f.ks_statistic}</span>
          ))}
        </div>
      )}
      {health.kind === "drift" && latest.explanation && <Summary>{latest.explanation}</Summary>}

      {live && (status?.shadow_version || pending) && (
        <div className="status-meta">
          {status?.shadow_version && <span className="chip">Challenger <strong>{status.shadow_version}</strong> in shadow</span>}
          {pending && <span className="chip"><strong>Promotion request #{pending.id}</strong> awaits your decision</span>}
        </div>
      )}
    </div>
  );
}
