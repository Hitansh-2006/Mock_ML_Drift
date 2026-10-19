# Drift-Watchdog Dashboard

React + Vite single-page app for the drift-watchdog pipeline: an overview
(KPIs, honest status, quick actions), the **Automation** start/stop card, the
drift monitor, promotion requests (approve/reject with confirmation) and recent
predictions. Light and dark themes. No UI or charting library: the two charts
(drift-score sparkline, production-vs-challenger bar) are small inline SVGs.

It talks to the serving API at `VITE_API_URL` (default `http://localhost:8000`)
and opens on a login page: username + password, or "Continue as guest" (read-only).
The session token is kept in the tab's sessionStorage, never in the bundle. Guests and
read-only accounts see everything, but every action is disabled in the UI and refused
by the server (`403`).

## Run it

```bash
# 1. project root: the backend (one worker)
uvicorn serving.app:app --port 8000

# 2. the dashboard
cd dashboard
npm ci
npm run dev          # http://localhost:5173
npm run lint         # oxlint, 0 errors / 0 warnings expected
npm run build
```

It polls every 5 s (2 s for a while after you start a background job) and
refreshes immediately after every action.

## What the status card may claim

| State | When |
|---|---|
| **Offline** (grey, "last updated hh:mm") | the API can't be reached; last data stays on screen, dimmed |
| **Locked** | the session has ended (401); the app returns to the login page |
| **Waiting for data** | reachable, but no drift check has ever run |
| **Normal** | only when the most recent real check did not trigger |
| **Drift detected** | the most recent check triggered |

Text that an LLM may have written is tagged "Summary - AI or template": the API
does not record which, so the UI doesn't claim "AI".

## Structure

- `src/api.js` - the one request helper (adds the session token, turns failures into errors with an HTTP status)
- `src/App.jsx` - the sign-in gate; `src/Console.jsx` - the dashboard itself, mounted only after login
- `src/hooks/useAuth.js` - session state (login, guest, sign out, restoring a saved session)
- `src/components/LoginPage.jsx` - the login page (animated "drift" illustration, show/hide password, caps-lock hint, lockout messages)
- `src/hooks/useDashboard.js` - polling + connection state (`online | offline | locked | degraded`); keeps stale data
- `src/hooks/useNow.js` - one shared timer for relative times and the countdown
- `src/lib/format.js`, `src/lib/health.js` - number/time formatting; the status decision
- `src/components/` - Header, StatusCard, KpiStrip, ActionsCard, AutomationCard, DriftMonitor, PromotionRequests, RecentPredictions, ConfirmDialog, Sparkline, CompareBar, `ui.jsx` (Button, Pill, Badge, Skeleton, ...)
- `src/index.css` - the design system: spacing / type / colour tokens for both themes, then primitives, then sections

## CORS

The API only allows the origins in `DASHBOARD_ORIGINS` (default: the local dev
server on port 5173). Set it when you serve the dashboard from somewhere else.
