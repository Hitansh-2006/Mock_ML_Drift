# Drift-Watchdog Dashboard

A plain, flat React dashboard for the drift-watchdog pipeline — status,
pending promotion requests (with approve/reject), drift monitor history, and
recent predictions. Light/dark theme toggle, no gradients, no shadows, no
glossy card styling — intentionally plain.

This talks directly to your local FastAPI backend at `http://localhost:8000`,
so it needs that running to show anything real.

## Run it

```bash
# 1. In one terminal, from the project root — start the backend
uvicorn serving.app:app --reload --port 8000

# 2. In another terminal — start the dashboard
cd dashboard
npm install
npm run dev
```

Then open the URL Vite prints (usually `http://localhost:5173`).

It polls the backend every 5 seconds, so drift events, new predictions, and
promotion requests appear automatically as your pipeline generates them —
no manual refresh needed.

## What's real vs. what's a placeholder

Everything shown is live data from your actual database — there's no mock
or sample data baked into the dashboard itself. If a section is empty, it's
because that part of the pipeline (drift monitor, retrain pipeline,
evaluation gate) hasn't been run yet against your current database.

## Structure

- `src/api.js` — all backend calls in one place
- `src/useTheme.js` — light/dark theme state, defaults to your OS preference
- `src/App.jsx` — the whole dashboard (status strip, promotions, drift
  events, predictions table)
- `src/index.css` — all styling; CSS custom properties drive both themes
  from one set of rules

## Note on CORS

`serving/app.py` now allows all origins for local development so the
dashboard (a different port) can call it from the browser. If you ever
deploy this beyond your own machine, tighten `allow_origins` in that file.
