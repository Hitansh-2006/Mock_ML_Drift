// Number and time formatting in one place so every table and card reads the same.

const MISSING = "—";

export function formatInt(value) {
  return value == null ? MISSING : value.toLocaleString();
}

export function formatMoney(value) {
  if (value == null) return MISSING;
  return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function formatMs(value) {
  return value == null ? MISSING : `${value.toFixed(1)} ms`;
}

export function formatPercent(fraction, digits = 1) {
  return fraction == null ? MISSING : `${(fraction * 100).toFixed(digits)}%`;
}

export function formatScore(value) {
  return value == null ? MISSING : value.toFixed(3);
}

export function formatClock(unixSeconds) {
  if (!unixSeconds) return MISSING;
  return new Date(unixSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function formatAbsolute(unixSeconds) {
  if (!unixSeconds) return "";
  return new Date(unixSeconds * 1000).toLocaleString();
}

/** "just now", "2 min ago", "3 h ago", "in 40 s" — `nowMs` comes from useNow so it stays current. */
export function formatRelative(unixSeconds, nowMs = Date.now()) {
  if (!unixSeconds) return MISSING;
  const diff = Math.round(nowMs / 1000 - unixSeconds);
  const abs = Math.abs(diff);
  let text;
  if (abs < 10) return "just now";
  if (abs < 60) text = `${abs} s`;
  else if (abs < 3600) text = `${Math.round(abs / 60)} min`;
  else if (abs < 86400) text = `${Math.round(abs / 3600)} h`;
  else text = `${Math.round(abs / 86400)} d`;
  return diff >= 0 ? `${text} ago` : `in ${text}`;
}

export function formatCountdown(seconds) {
  if (seconds <= 0) return "any moment";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

export function formatInterval(seconds) {
  if (seconds == null) return MISSING;
  return seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`;
}

/** Fractional improvement of the challenger over production (positive = better). */
export function improvement(productionMae, challengerMae) {
  if (!productionMae || challengerMae == null) return null;
  return (productionMae - challengerMae) / productionMae;
}
