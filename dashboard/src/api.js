// Reads from a .env file (VITE_API_URL=https://your-real-server.com) at
// build time, so the same dashboard code works locally (defaults to
// localhost) and when deployed against a real, remote serving API.
export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// The session token (from /auth/login or /auth/guest) is kept in this browser's
// sessionStorage: it disappears when the tab closes and is never baked into the
// build. The server stores only a hash of it and expires it on its own.
const TOKEN_STORAGE = "drift_watchdog_session";

export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_STORAGE, token);
    else sessionStorage.removeItem(TOKEN_STORAGE);
  } catch {
    /* storage unavailable (private mode etc.) - the session just won't survive a reload */
  }
}

// One request helper for everything: adds the session token, and turns every
// failure into an Error that carries the HTTP status (401 = not signed in or
// session expired, 403 = read-only account, 409 = refused because of current
// state, 429 = too many sign-in attempts, 503 = server not set up).
async function request(method, path, { json, formData } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
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
  // sign-in
  authConfig: () => get("/auth/config"),
  login: (username, password) => post("/auth/login", { username, password }),
  signup: (username, password, role = "admin") => post("/auth/signup", { username, password, role }),
  guest: () => postEmpty("/auth/guest"),
  me: () => get("/auth/me"),
  logout: () => postEmpty("/auth/logout"),

  // existing endpoints
  getStatus: () => get("/status"),
  getPredictions: (limit = 50) => get(`/admin/predictions?limit=${limit}`),
  getDriftEvents: (limit = 20) => get(`/admin/drift_events?limit=${limit}`),
  getPromotionRequests: () => get("/admin/promotion_requests"),
  decidePromotion: (promotion_request_id, decision) =>
    post("/admin/promote", { promotion_request_id, decision }), // the server records who is signed in

  // KPI stats and action buttons
  getStats: () => get("/admin/stats"),
  uploadStream: (file) => postFile("/admin/upload_stream", file),
  trainBaseline: () => postEmpty("/admin/train_baseline"),
  runDriftCheck: () => postEmpty("/admin/run_drift_check"),
  retrain: () => postEmpty("/admin/retrain"),

  // automation loop (Start/Stop card)
  getAutomation: () => get("/admin/automation"),
  startAutomation: (interval_seconds) =>
    post("/admin/automation/start", interval_seconds ? { interval_seconds } : {}),
  stopAutomation: () => postEmpty("/admin/automation/stop"),
  runAutomationNow: () => postEmpty("/admin/automation/run_now"),
};
