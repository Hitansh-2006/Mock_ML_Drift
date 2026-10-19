import { formatClock } from "./format";

/**
 * The one place that decides what the STATUS card may claim. "Normal" is only
 * ever returned when a real drift check exists and did not trigger; offline,
 * locked and not-yet-checked are never dressed up as healthy.
 *
 * kind: loading | offline | locked | waiting | normal | drift
 */
export function deriveHealth({ conn, driftEvents }) {
  if (conn.state === "offline") {
    return {
      kind: "offline",
      label: "Offline",
      headline: "Can't reach the serving API",
      detail: conn.lastOkAt
        ? `Last updated ${formatClock(conn.lastOkAt)}. What you see below is from then and may be out of date.`
        : "The dashboard has not been able to connect yet.",
    };
  }
  if (conn.state === "locked") {
    return {
      kind: "locked",
      label: "Locked",
      headline: "Signed out",
      detail: "Your session has ended. Sign in again to see drift checks, promotions and automation.",
    };
  }
  if (driftEvents == null) {
    return { kind: "loading", label: "Checking…", headline: "", detail: "" };
  }
  if (driftEvents.length === 0) {
    return {
      kind: "waiting",
      label: "Waiting for data",
      headline: "No drift check has run yet",
      detail: "Nothing can be called normal until the monitor has looked at live traffic. Send some requests, then run a drift check or start Automation.",
    };
  }
  const latest = driftEvents[0];
  if (latest.triggered) {
    const n = Object.values(latest.details || {}).filter((f) => f.drifted).length;
    return {
      kind: "drift",
      label: "Drift detected",
      headline: `Live inputs have shifted on ${n} feature${n === 1 ? "" : "s"}`,
      detail: "The latest check found a statistically significant difference from the training data.",
      latest,
    };
  }
  return {
    kind: "normal",
    label: "Normal",
    headline: "Latest check found no drift",
    detail: "The most recent window of live requests matches the training distribution closely enough. This reflects the last check only.",
    latest,
  };
}

export const HEALTH_TONE = {
  loading: "neutral",
  offline: "neutral",
  locked: "neutral",
  waiting: "warning",
  normal: "success",
  drift: "danger",
};
