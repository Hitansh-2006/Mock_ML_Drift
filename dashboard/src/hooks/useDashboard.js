import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_URL } from "../api";

const POLL_MS = 5000;
const FAST_POLL_MS = 2000;

const EMPTY = {
  status: null,
  stats: null,
  predictions: null,
  driftEvents: null,
  promotions: null,
  automation: null,
};

// resource name -> request. `null` in state means "not loaded yet" (skeleton),
// which is different from an empty list (loaded, nothing there).
const RESOURCES = {
  status: () => api.getStatus(),
  stats: () => api.getStats(),
  predictions: () => api.getPredictions(50),
  driftEvents: () => api.getDriftEvents(30),
  promotions: () => api.getPromotionRequests(),
  automation: () => api.getAutomation(),
};

/**
 * Polls the API and tracks how trustworthy what's on screen is.
 *   conn.state: "loading" | "online" | "offline" | "locked" (401) | "degraded" (other HTTP error)
 * Data from the last good response is kept on a failure (the UI dims it and
 * shows `conn.lastOkAt`), never silently replaced or presented as current.
 */
export function useDashboard(onUnauthorized) {
  const [data, setData] = useState(EMPTY);
  const [conn, setConn] = useState({ state: "loading", lastOkAt: null, message: null });
  const fastUntil = useRef(0);
  const unauthorized = useRef(onUnauthorized);
  useEffect(() => {
    unauthorized.current = onUnauthorized; // always the latest callback, without re-creating `refresh`
  });

  const refresh = useCallback(async () => {
    const names = Object.keys(RESOURCES);
    const results = await Promise.allSettled(names.map((n) => RESOURCES[n]()));

    const fresh = {};
    const failures = [];
    results.forEach((r, i) => {
      if (r.status === "fulfilled") fresh[names[i]] = r.value;
      else failures.push(r.reason);
    });
    setData((prev) => ({ ...prev, ...fresh }));

    const network = failures.find((e) => !e.status);
    const rejected = failures.find((e) => e.status === 401);
    const other = failures.find((e) => e.status && e.status !== 401);
    if (network) {
      setConn((c) => ({
        state: "offline",
        lastOkAt: c.lastOkAt,
        message: `Could not reach the serving API at ${API_URL}. Is it running?`,
      }));
    } else if (rejected) {
      // The session is no longer valid (expired, revoked, server reset): back to the login page.
      setConn((c) => ({ state: "locked", lastOkAt: c.lastOkAt, message: "Your session has ended." }));
      unauthorized.current?.();
    } else if (other) {
      setConn((c) => ({ state: "degraded", lastOkAt: c.lastOkAt, message: other.message }));
    } else {
      setConn({ state: "online", lastOkAt: Date.now() / 1000, message: null });
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer;
    const tick = async () => {
      await refresh();
      if (stopped) return;
      timer = setTimeout(tick, Date.now() < fastUntil.current ? FAST_POLL_MS : POLL_MS);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  /** Poll faster for a while, e.g. while a background job is expected to finish. */
  const boost = useCallback((ms = 60000) => {
    fastUntil.current = Date.now() + ms;
  }, []);

  return { data, conn, refresh, boost };
}
