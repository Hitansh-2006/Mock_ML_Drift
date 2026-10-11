import { useSyncExternalStore } from "react";

// One shared 1 s timer for every component that shows a relative time or a
// countdown, instead of one setInterval per table cell.
const listeners = new Set();
let timer = null;

function subscribe(listener) {
  listeners.add(listener);
  if (!timer) timer = setInterval(() => listeners.forEach((l) => l()), 1000);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** Current time in ms, re-rendering the caller only when it crosses a `stepSeconds` boundary. */
export function useNow(stepSeconds = 1) {
  const bucket = useSyncExternalStore(
    subscribe,
    () => Math.floor(Date.now() / (stepSeconds * 1000)),
    () => 0
  );
  return bucket * stepSeconds * 1000;
}
