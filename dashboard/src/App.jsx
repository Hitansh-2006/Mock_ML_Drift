import { useTheme } from "./useTheme";
import { useAuth } from "./hooks/useAuth";
import { LoginPage } from "./components/LoginPage";
import Console from "./Console";

/** Sign-in gate: the dashboard (and every request it makes) only exists after a login or guest session. */
export default function App() {
  const [theme, setTheme] = useTheme();
  const auth = useAuth();

  if (auth.status === "checking") {
    return (
      <div className="auth-splash" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span>Restoring your session…</span>
      </div>
    );
  }
  if (auth.status !== "authed") {
    return <LoginPage auth={auth} theme={theme} setTheme={setTheme} />;
  }
  return <Console user={auth.user} onSignOut={auth.signOut} theme={theme} setTheme={setTheme} />;
}
