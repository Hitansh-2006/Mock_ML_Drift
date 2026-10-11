// Reads from a .env file (VITE_API_URL=https://your-real-server.com) at
// build time, so the same dashboard code works locally (defaults to
// localhost) and when deployed against a real, remote serving API.
export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// The admin key is typed into the dashboard once and kept in this browser's
// localStorage. It is deliberately NOT baked into the build: anything compiled
// into the JavaScript bundle is readable by every visitor.
const KEY_STORAGE = "drift_watchdog_admin_key";

export function getAdminKey() {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setAdminKey(key) {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable (private mode etc.) - the key just won't persist */
  }
}

// One request helper for everything: adds the key, and turns every failure
// into an Error that carries the HTTP status (401 = wrong/missing key,
// 409 = action refused because of current state, 503 = server locked).
async function request(method, path, { json, formData } = {}) {
  const headers = {};
  const key = getAdminKey();
  if (key) headers["X-API-Key"] = key;
  if (json !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: json !== undefined ? JSON.stringify(json) : formData,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const err = new Error(
      typeof detail.detail === "string"
        ? detail.detail
        : `${method} ${path} failed: ${res.status}`
    );
    err.status = res.status;
    throw err;
  }
  return res.json();
}

const get = (path) => request("GET", path);
const post = (path, body) => request("POST", path, { json: body });
const postEmpty = (path) => request("POST", path);
const postFile = (path, file) => {
  const formData = new FormData();
  formData.append("file", file);
  return request("POST", path, { formData });
};

export const api = {
  // existing endpoints
  getStatus: () => get("/status"),
  getPredictions: (limit = 50) => get(`/admin/predictions?limit=${limit}`),
  getDriftEvents: (limit = 20) => get(`/admin/drift_events?limit=${limit}`),
  getPromotionRequests: () => get("/admin/promotion_requests"),
  decidePromotion: (promotion_request_id, decision) =>
    post("/admin/promote", { promotion_request_id, decision, decided_by: "dashboard" }),

  // KPI stats and action buttons
  getStats: () => get("/admin/stats"),
  uploadStream: (file) => postFile("/admin/upload_stream", file),
  trainBaseline: () => postEmpty("/admin/train_baseline"),
  runDriftCheck: () => postEmpty("/admin/run_drift_check"),
  retrain: () => postEmpty("/admin/retrain"),
};
