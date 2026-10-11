# Drift-Watchdog Dashboard

React + Vite single-page app for the drift-watchdog pipeline: an overview
(KPIs, honest status, quick actions), the **Automation** start/stop card, the
drift monitor, promotion requests (approve/reject with confirmation) and recent
predictions. Light and dark themes. No UI or charting library: the two charts
(drift-score sparkline, production-vs-challenger bar) are small inline SVGs.

It talks to the serving API at `VITE_API_URL` (default `http://localhost:8000`)
and asks for the admin key the first time (kept in this browser's localStorage,
never in the bundle).

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
| **Locked** | the admin key is missing/wrong (401) |
| **Waiting for data** | reachable, but no drift check has ever run |
| **Normal** | only when the most recent real check did not trigger |
| **Drift detected** | the most recent check triggered |

Text that an LLM may have written is tagged "Summary - AI or template": the API
does not record which, so the UI doesn't claim "AI".

## Structure

- `src/api.js` - the one request helper (adds the key, turns failures into errors with an HTTP status)
- `src/hooks/useDashboard.js` - polling + connection state (`online | offline | locked | degraded`); keeps stale data
- `src/hooks/useNow.js` - one shared timer for relative times and the countdown
- `src/lib/format.js`, `src/lib/health.js` - number/time formatting; the status decision
- `src/components/` - Header, StatusCard, KpiStrip, ActionsCard, AutomationCard, DriftMonitor, PromotionRequests, RecentPredictions, ConfirmDialog, Sparkline, CompareBar, `ui.jsx` (Button, Pill, Badge, Skeleton, ...)
- `src/index.css` - the design system: spacing / type / colour tokens for both themes, then primitives, then sections

## CORS

The API only allows the origins in `DASHBOARD_ORIGINS` (default: the local dev
server on port 5173). Set it when you serve the dashboard from somewhere else.
