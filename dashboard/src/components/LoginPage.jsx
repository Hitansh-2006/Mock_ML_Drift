import { useEffect, useRef, useState } from "react";
import { api, API_URL } from "../api";
import { Button, Spinner } from "./ui";

/* ---- small inline icons (decorative: aria-hidden) ---- */
const icon = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const UserIcon = () => <svg {...icon}><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>;
const LockIcon = () => <svg {...icon}><rect x="4" y="10" width="16" height="11" rx="2.5" /><path d="M8 10V7a4 4 0 018 0v3" /></svg>;
const EyeIcon = ({ off }) => (
  <svg {...icon}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />{off && <path d="M4 4l16 16" />}</svg>
);
const GuestIcon = () => <svg {...icon}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></svg>;
const CheckIcon = () => <svg {...icon} width="16" height="16"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;

function Logo({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6l8-3z" />
      <path d="M8 12h2.5l1.5-3 2 6 1.5-3H17" />
    </svg>
  );
}

/**
 * The idea of the product in one calm picture: the distribution the model was
 * trained on (steady) and live traffic (slowly sliding away from it).
 */
function DriftArt() {
  const bell = "M24 172 C 78 172 112 38 180 38 C 248 38 282 172 336 172";
  return (
    <svg className="drift-art" viewBox="0 0 400 210" role="img" aria-label="Illustration: live traffic drifting away from the training distribution">
      <defs>
        <linearGradient id="ref-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.22" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="live-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="#5cc4ff" stopOpacity="0.4" />
          <stop offset="1" stopColor="#5cc4ff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <g className="art-grid" stroke="#fff" strokeOpacity="0.08">
        {[50, 90, 130].map((y) => <line key={y} x1="10" x2="390" y1={y} y2={y} />)}
      </g>
      <line x1="10" x2="390" y1="172" y2="172" stroke="#fff" strokeOpacity="0.35" />
      <g>
        <path d={`${bell} L 336 172 L 24 172 Z`} fill="url(#ref-fill)" />
        <path d={bell} fill="none" stroke="#fff" strokeOpacity="0.85" strokeWidth="2.5" strokeLinecap="round" />
      </g>
      <g className="art-live">
        <path d={`${bell} L 336 172 L 24 172 Z`} fill="url(#live-fill)" />
        <path d={bell} fill="none" stroke="#5cc4ff" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="7 6" />
        <circle className="art-ping" cx="180" cy="38" r="5" fill="#5cc4ff" />
        <circle className="art-ping-ring" cx="180" cy="38" r="5" fill="none" stroke="#5cc4ff" />
      </g>
      <g fontSize="11" fontWeight="600" fill="#fff" fillOpacity="0.8">
        <text x="24" y="196">Training data</text>
        <text x="296" y="196" fill="#5cc4ff" fillOpacity="1">Live traffic</text>
      </g>
    </svg>
  );
}

const POINTS = [
  "Spots data drift on live traffic as it happens",
  "Tests a replacement model silently, in shadow",
  "Promotes only what a human approves",
];

/** Turn an API/network error into something a person can act on. */
function explain(e) {
  if (!e.status) return `Can't reach the server at ${API_URL}. Check that it is running.`;
  if (e.status === 422) return "Enter your username and password.";
  if (e.status === 503) return "Sign-in isn't set up on this server yet. Ask whoever runs it to create an account.";
  return e.message;
}

export function LoginPage({ auth, theme, setTheme }) {
  const [cfg, setCfg] = useState({ state: "loading" }); // loading | ready | offline
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [caps, setCaps] = useState(false);
  const [busy, setBusy] = useState(null); // signin | guest
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0); // re-keys the error so its shake replays
  const userRef = useRef(null);
  const passRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    api
      .authConfig()
      .then((c) => !cancelled && setCfg({ state: "ready", ...c }))
      .catch(() => !cancelled && setCfg({ state: "offline" }));
    userRef.current?.focus();
    return () => {
      cancelled = true;
    };
  }, []);

  const retry = () => {
    setCfg({ state: "loading" });
    api
      .authConfig()
      .then((c) => setCfg({ state: "ready", ...c }))
      .catch(() => setCfg({ state: "offline" }));
  };

  // Focus can only move once the inputs are enabled again, i.e. after the next render.
  const focusAfter = useRef(null);
  useEffect(() => {
    focusAfter.current?.current?.focus();
    focusAfter.current = null;
  }, [attempt]);

  const fail = (e, field) => {
    setError(explain(e));
    focusAfter.current = field ?? null;
    setAttempt((n) => n + 1);
    setBusy(null);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      focusAfter.current = username.trim() ? passRef : userRef;
      setAttempt((n) => n + 1);
      return;
    }
    setBusy("signin");
    setError(null);
    try {
      await auth.login(username.trim(), password);
    } catch (e) {
      setPassword("");
      fail(e, passRef);
    }
  };

  const continueAsGuest = async () => {
    if (busy) return;
    setBusy("guest");
    setError(null);
    try {
      await auth.guest();
    } catch (e) {
      fail(e);
    }
  };

  const notConfigured = cfg.state === "ready" && !cfg.configured;
  const formDisabled = cfg.state !== "ready" || notConfigured;
  const checkCaps = (e) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  return (
    <div className="auth-shell">
      <aside className="auth-hero">
        <div className="hero-glow" aria-hidden="true" />
        <div className="hero-brand rise" style={{ "--d": "0.05s" }}><Logo size={30} /><span>Drift-Watchdog</span></div>

        <div className="hero-body">
          <p className="hero-title rise" style={{ "--d": "0.15s" }}>Know when your model's world changes.</p>
          <p className="hero-sub rise" style={{ "--d": "0.25s" }}>
            Watch a deployed model for drift, train and test a replacement on real traffic, and promote it only when you say so.
          </p>
          <div className="rise" style={{ "--d": "0.35s" }}><DriftArt /></div>
          <ul className="hero-points">
            {POINTS.map((text, i) => (
              <li key={text} className="rise" style={{ "--d": `${0.5 + i * 0.12}s` }}><span className="tick"><CheckIcon /></span>{text}</li>
            ))}
          </ul>
        </div>
        <p className="hero-foot rise" style={{ "--d": "0.9s" }}>Promotions always need human approval.</p>
      </aside>

      <main className="auth-panel">
        <div className="auth-top">
          <div className="segmented" role="group" aria-label="Colour theme">
            <button type="button" aria-pressed={theme === "light"} aria-label="Light theme" title="Light theme" onClick={() => setTheme("light")}>☀</button>
            <button type="button" aria-pressed={theme === "dark"} aria-label="Dark theme" title="Dark theme" onClick={() => setTheme("dark")}>☾</button>
          </div>
        </div>

        <div className="auth-card rise" style={{ "--d": "0.1s" }}>
          <div className="auth-mark" aria-hidden="true"><Logo size={26} /></div>
          <h1>Sign in</h1>
          <p className="auth-sub">to the Drift-Watchdog console</p>

          {auth.notice && <div className="auth-alert" data-tone="info" role="status">{auth.notice}</div>}
          {cfg.state === "offline" && (
            <div className="auth-alert" data-tone="warning" role="alert">
              <span>Can't reach the server at {API_URL}.</span>
              <button type="button" className="link-btn" onClick={retry}>Try again</button>
            </div>
          )}
          {notConfigured && (
            <div className="auth-alert" data-tone="warning" role="alert">
              No account exists yet. Set <code>ADMIN_USERNAME</code> and <code>ADMIN_PASSWORD</code> in <code>.env</code> and restart the API.
            </div>
          )}

          <form onSubmit={submit} noValidate aria-describedby={error ? "auth-error" : undefined}>
            <div className="field-block">
              <label htmlFor="login-username">Username</label>
              <div className="input-wrap">
                <span className="input-icon"><UserIcon /></span>
                <input
                  id="login-username" ref={userRef} type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
                  value={username} onChange={(e) => setUsername(e.target.value)} disabled={formDisabled || !!busy}
                  aria-invalid={error ? true : undefined}
                />
              </div>
            </div>

            <div className="field-block">
              <label htmlFor="login-password">Password</label>
              <div className="input-wrap">
                <span className="input-icon"><LockIcon /></span>
                <input
                  id="login-password" ref={passRef} type={reveal ? "text" : "password"} autoComplete="current-password"
                  value={password} onChange={(e) => setPassword(e.target.value)} onKeyUp={checkCaps} onKeyDown={checkCaps} onBlur={() => setCaps(false)}
                  disabled={formDisabled || !!busy} aria-invalid={error ? true : undefined}
                />
                <button
                  type="button" className="reveal" onClick={() => setReveal((v) => !v)}
                  aria-pressed={reveal} aria-label={reveal ? "Hide password" : "Show password"} title={reveal ? "Hide password" : "Show password"}
                  disabled={formDisabled}
                >
                  <EyeIcon off={reveal} />
                </button>
              </div>
              {caps && <p className="caps-hint" role="status">Caps Lock is on</p>}
            </div>

            {error && <div key={attempt} id="auth-error" className="auth-error" role="alert">{error}</div>}

            <button type="submit" className="btn btn-primary auth-submit" disabled={formDisabled || !!busy}>
              {busy === "signin" ? <><Spinner /> Signing in…</> : "Sign in"}
            </button>
          </form>

          {cfg.state === "ready" && cfg.guest_enabled && !notConfigured && (
            <>
              <div className="auth-divider"><span>or</span></div>
              <Button className="auth-guest" loading={busy === "guest"} loadingLabel="Opening guest view…" disabledReason={busy === "signin" ? "Signing in…" : undefined} onClick={continueAsGuest}>
                <GuestIcon /> Continue as guest
              </Button>
              <p className="auth-hint">Look around in read-only mode. No account needed, and nothing can be changed.</p>
            </>
          )}
        </div>

        <p className="auth-foot">Sessions end automatically. Sign out when you're done on a shared computer.</p>
      </main>
    </div>
  );
}
