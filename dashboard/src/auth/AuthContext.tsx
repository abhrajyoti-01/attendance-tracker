import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, clearTokens, getAccessToken, storeTokens } from "../api/client";
import type { LoginInput, TokenResponse } from "../api/client";
import type { UserProfile } from "../api/types";

interface AuthState {
  userId: string | null;
  userName: string | null;
  role: string | null;
  isAdmin: boolean;
  isSuperadmin: boolean;
  /** False while the stored session is being validated on boot. */
  ready: boolean;
  /** Set when the session ended involuntarily, so the login page can explain. */
  expiredNotice: boolean;
  clearExpiredNotice: () => void;
  login: (input: LoginInput) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function toTokenResponse(access: string, user: UserProfile): TokenResponse {
  return {
    access_token: access,
    refresh_token: "",
    token_type: "bearer",
    expires_in: 0,
    user_id: user.id,
    user_name: user.name,
    organization_id: user.department_id ? "" : "",
    role: user.role,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokenData, setTokenData] = useState<TokenResponse | null>(null);
  const [ready, setReady] = useState(false);
  const [expiredNotice, setExpiredNotice] = useState(false);

  useEffect(() => {
    const access = getAccessToken();
    if (!access) {
      setReady(true);
      return;
    }

    let cancelled = false;
    // /users/me is the source of truth for display metadata and doubles as a
    // session-liveness check. A refresh may run internally on 401.
    api
      .me()
      .then((user) => {
        if (cancelled) return;
        setTokenData(toTokenResponse(getAccessToken() ?? access, user));
      })
      .catch((error: { status?: number }) => {
        if (cancelled) return;
        // Only discard the stored session when the server definitively
        // rejected it. A network blip must not log the user out.
        if (error?.status === 401 || error?.status === 403) {
          clearTokens();
          setTokenData(null);
        } else if (getAccessToken()) {
          setExpiredNotice(true);
        }
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onExpired = () => {
      clearTokens();
      setTokenData(null);
      setExpiredNotice(true);
    };
    // Another tab logging out must not leave this tab believing it is signed in.
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== "at.access_token") return;
      if (!getAccessToken()) {
        setTokenData(null);
      }
    };
    window.addEventListener("at:session-expired", onExpired);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("at:session-expired", onExpired);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const login = useCallback(async (input: LoginInput) => {
    const tokens = await api.login(input);
    storeTokens(tokens.access_token, tokens.refresh_token);
    setExpiredNotice(false);
    setTokenData(tokens);
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    clearTokens();
    setTokenData(null);
    setExpiredNotice(false);
  }, []);

  const clearExpiredNotice = useCallback(() => setExpiredNotice(false), []);

  const value = useMemo<AuthState>(
    () => ({
      userId: tokenData?.user_id ?? null,
      userName: tokenData?.user_name ?? null,
      role: tokenData?.role ?? null,
      isAdmin: tokenData?.role === "org_admin" || tokenData?.role === "superadmin",
      isSuperadmin: tokenData?.role === "superadmin",
      ready,
      expiredNotice,
      clearExpiredNotice,
      login,
      logout,
    }),
    [tokenData, ready, expiredNotice, clearExpiredNotice, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
