import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, clearTokens, getAccessToken, storeTokens } from "../api/client";
import type { TokenResponse } from "../api/client";

interface AuthState {
  userId: string | null;
  userName: string | null;
  role: string | null;
  isAdmin: boolean;
  login: (input: {
    email: string;
    password: string;
    organization_slug: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokenData, setTokenData] = useState<TokenResponse | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const access = getAccessToken();
    if (!access) {
      setReady(true);
      return;
    }
    // The JWT carries only sub/org_id; /users/me is the source of truth for
    // display metadata (name, role) and doubles as a session-liveness check.
    api
      .me()
      .then((user) =>
        setTokenData({
          access_token: access,
          refresh_token: "",
          token_type: "bearer",
          expires_in: 0,
          user_id: user.id,
          user_name: user.name,
          organization_id: "",
          role: user.role,
        }),
      )
      .catch(() => clearTokens())
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    const onExpired = () => setTokenData(null);
    window.addEventListener("at:session-expired", onExpired);
    return () => window.removeEventListener("at:session-expired", onExpired);
  }, []);

  const login = useCallback(
    async (input: { email: string; password: string; organization_slug: string }) => {
      const tokens = await api.login(input);
      storeTokens(tokens.access_token, tokens.refresh_token);
      setTokenData(tokens);
    },
    [],
  );

  const logout = useCallback(async () => {
    await api.logout();
    clearTokens();
    setTokenData(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      userId: tokenData?.user_id ?? null,
      userName: tokenData?.user_name ?? null,
      role: tokenData?.role ?? null,
      isAdmin: tokenData?.role === "org_admin" || tokenData?.role === "superadmin",
      login,
      logout,
    }),
    [tokenData, login, logout],
  );

  if (!ready) return null;
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
