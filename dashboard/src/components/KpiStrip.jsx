import { formatInt, formatMoney, formatMs, formatScore } from "../lib/format";
import { RelativeTime, Skeleton } from "./ui";

function Kpi({ label, loading, value, sub, tone }) {
  return (
    <div className="card kpi" aria-busy={loading || undefined}>
      <div className="card-title">{label}</div>
      {loading ? (
        <>
          <Skeleton width="55%" height={30} />
          <Skeleton width="75%" height={14} />
        </>
      ) : (
        <>
          <div className="kpi-value" data-tone={tone || undefined}>{value}</div>
          <div className="kpi-sub">{sub}</div>
        </>
      )}
    </div>
  );
}

const NA = <span className="kpi-note">Unavailable</span>;

/**
 * How bad is the live error? Judged against what THIS model scored on its own validation split
 * (no house-price constants); the warn/alert ratios come from the server's config.
 * Returns null when there is nothing to compare.
 */
function judgeError(stats) {
  if (!stats || stats.production_mae == null || !stats.expected_mae) return null;
  const ratio = stats.production_mae / stats.expected_mae;
  const tone = ratio >= stats.mae_alert_ratio ? "danger" : ratio >= stats.mae_warn_ratio ? "warning" : "success";
  return { ratio, tone };
}

function latencyText(stats) {
  if (stats.production_latency_ms == null) return "inference not timed yet";
  return `inference p50 ${formatMs(stats.production_latency_ms)}, p95 ${formatMs(stats.production_latency_p95_ms)}`;
}

/**
 * `unavailable` = we are not loading any more but have nothing (offline or
 * locked before the first success): say so instead of a skeleton forever.
 */
export function KpiStrip({ status, stats, latestDrift, driftLoaded, unavailable }) {
  const statsLoading = stats == null && !unavailable;
  const driftLoading = !driftLoaded && !unavailable;
  const noLabels = stats && stats.production_mae == null;

  return (
    <div className="grid-kpi">
      <Kpi
        label="Production model"
        loading={status == null && !unavailable}
        value={status?.production_version ?? NA}
        sub={status ? (status.shadow_version ? `Shadow: ${status.shadow_version}` : "No challenger in shadow") : "—"}
      />
      <Kpi
        label="Predictions"
        loading={statsLoading}
        value={stats ? formatInt(stats.total_predictions) : NA}
        sub={stats ? `${formatInt(stats.total_labeled)} labelled so far` : "—"}
      />
      <Kpi
        label={stats?.production_version ? `Error (MAE) · ${stats.production_version}` : "Error (MAE)"}
        loading={statsLoading}
        tone={judgeError(stats)?.tone}
        value={!stats ? NA : noLabels ? <span className="kpi-note">No labels yet</span> : formatMoney(stats.production_mae)}
        sub={
          !stats ? "—"
            : noLabels ? `Labels arrive after a delay · ${latencyText(stats)}`
              : <>
                {judgeError(stats)
                  ? `${judgeError(stats).ratio.toFixed(1)}× its validation error (${formatMoney(stats.expected_mae)})`
                  : `last ${stats.production_mae_n} labelled`}
                {" · "}{latencyText(stats)}
              </>
        }
      />
      <Kpi
        label="Latest drift score"
        loading={driftLoading}
        value={latestDrift ? formatScore(latestDrift.drift_score) : driftLoaded ? <span className="kpi-note">No checks yet</span> : NA}
        sub={
          latestDrift ? (
            <>
              {latestDrift.threshold != null && <>per-feature KS threshold {latestDrift.threshold} · </>}
              <RelativeTime ts={latestDrift.checked_at} />
            </>
          ) : driftLoaded ? "Run a drift check or start Automation" : "—"
        }
      />
    </div>
  );
}
