import { formatClock } from "../lib/format";
import { RelativeTime } from "./ui";

function connectionText(conn) {
  switch (conn.state) {
    case "online":
      return (
        <>
          Live<span className="conn-text-long"> · updated <RelativeTime ts={conn.lastOkAt} /></span>
        </>
      );
    case "offline":
      return (
        <>
          Offline<span className="conn-text-long"> · last updated {conn.lastOkAt ? formatClock(conn.lastOkAt) : "never"}</span>
        </>
      );
    case "locked":
      return "Admin key needed";
    case "degraded":
      return "Partial data";
    default:
      return "Connecting…";
  }
}

function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6l8-3z" />
      <path d="M8 12h2.5l1.5-3 2 6 1.5-3H17" />
    </svg>
  );
}

export function Header({ conn, theme, setTheme, user, onSignOut }) {
  return (
    <header className="app-header">
      <div className="app-header-inner">
        <h1 className="brand">
          <Logo />
          <span>Drift-Watchdog</span>
        </h1>
        <nav className="app-nav" aria-label="Sections">
          <a href="#overview">Overview</a>
          <a href="#automation">Automation</a>
          <a href="#drift">Drift monitor</a>
          <a href="#promotions">Promotions</a>
          <a href="#predictions">Predictions</a>
        </nav>
        <div className="header-right">
          <span className="conn" data-state={conn.state} role="status" aria-live="polite">
            <span className="dot" aria-hidden="true" />
            <span>{connectionText(conn)}</span>
          </span>
          <div className="segmented" role="group" aria-label="Colour theme">
            <button type="button" aria-pressed={theme === "light"} aria-label="Light theme" title="Light theme" onClick={() => setTheme("light")}>☀</button>
            <button type="button" aria-pressed={theme === "dark"} aria-label="Dark theme" title="Dark theme" onClick={() => setTheme("dark")}>☾</button>
          </div>
          {user && (
            <div className="user-menu">
              <span className="user-chip" title={`Signed in as ${user.username} (${user.guest ? "guest, read-only" : user.role})`}>
                <span className="avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span>
                <span className="user-name">{user.guest ? "Guest" : user.username}</span>
                <span className="badge" data-tone={user.role === "admin" ? "accent" : "neutral"}>{user.role === "admin" ? "Admin" : "Read-only"}</span>
              </span>
              <button type="button" className="btn btn-secondary btn-sm" onClick={onSignOut}>Sign out</button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
