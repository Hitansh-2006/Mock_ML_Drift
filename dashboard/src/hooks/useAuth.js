import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken } from "../api";

/**
 * Who is signed in.
 *   status: "checking" (a saved session is being verified) | "anon" | "authed"
 * A saved token is verified once with /auth/me, so an expired or revoked session
 * lands on the login page instead of a dashboard full of 401s.
 */
export function useAuth() {
  const [state, setState] = useState(() => ({
    status: getToken() ? "checking" : "anon",
    user: null,
    notice: null, // shown on the login page, e.g. "Your session expired"
  }));

  useEffect(() => {
    if (!getToken()) return undefined;
    let cancelled = false;
    api
      .me()
      .then((user) => !cancelled && setState({ status: "authed", user, notice: null }))
      .catch((e) => {
        if (cancelled) return;
        if (e.status === 401) setToken("");
        setState({
          status: "anon",
          user: null,
          notice: e.status === 401 ? "Your session has ended. Please sign in again." : null,
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const adopt = useCallback((session) => {
    setToken(session.token);
    setState({ status: "authed", user: session.user, notice: null });
  }, []);

  /** Throws on failure so the login form can show the server's message. */
  const login = useCallback(async (username, password) => adopt(await api.login(username, password)), [adopt]);
  const guest = useCallback(async () => adopt(await api.guest()), [adopt]);

  const signOut = useCallback(async (notice = null) => {
    try {
      await api.logout();
    } catch {
      /* already invalid or API down: signing out locally is what matters */
    }
    setToken("");
    setState({ status: "anon", user: null, notice });
  }, []);

  return { ...state, login, guest, signOut };
}
