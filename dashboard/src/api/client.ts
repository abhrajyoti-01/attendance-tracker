/** Typed API client with access/refresh token rotation and SSE streaming. */

const API_BASE = "/api";
const ACCESS_KEY = "at.access_token";
const REFRESH_KEY = "at.refresh_token";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
  user_name: string;
  organization_id: string;
  role: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function storeTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

async function refreshTokens(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!response.ok) {
    clearTokens();
    return false;
  }
  const data = (await response.json()) as TokenResponse;
  storeTokens(data.access_token, data.refresh_token);
  return true;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const access = getAccessToken();
  if (access) headers.set("Authorization", `Bearer ${access}`);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Network error - is the API server running?");
  }

  if (response.status === 401 && retry && getRefreshToken()) {
    // Rotation invalidates the presented token on every refresh; only ever
    // refresh once per call (a replayed rotated token revokes all sessions).
    if (await refreshTokens()) {
      return request<T>(path, init, false);
    }
    window.dispatchEvent(new CustomEvent("at:session-expired"));
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep default detail */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/* ---------- typed endpoint helpers ---------- */

export const api = {
  login: (input: { email: string; password: string; organization_slug: string }) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  logout: () =>
    request<{ message?: string }>("/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: getRefreshToken() ?? "" }),
    }).catch(() => undefined),

  me: () => request<import("./types").UserProfile>("/users/me"),

  summary: () => request<import("./types").DashboardSummary>("/dashboard/summary"),
  dailyChart: (days: number) =>
    request<import("./types").DailyChart>(`/dashboard/chart/daily?days=${days}`),
  hourlyChart: () => request<import("./types").HourlyChart>("/dashboard/chart/hours"),
  departmentChart: () => request<import("./types").DepartmentChart>("/dashboard/chart/department"),
  feed: (limit = 50) => request<import("./types").FeedResponse>(`/dashboard/feed?limit=${limit}`),

  users: (page = 1, pageSize = 50) =>
    request<import("./types").UserList>(`/users?page=${page}&page_size=${pageSize}`),
  createUser: (body: Record<string, unknown>) =>
    request("/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: string, body: Record<string, unknown>) =>
    request(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  departments: () => request<import("./types").DepartmentList>("/departments"),
  createDepartment: (name: string) =>
    request("/departments", { method: "POST", body: JSON.stringify({ name }) }),
  deleteDepartment: (id: string) => request(`/departments/${id}`, { method: "DELETE" }),

  settings: () => request<import("./types").OrgSettings>("/dashboard/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request<import("./types").OrgSettings>("/dashboard/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
};

/**
 * Consume the admin live-feed SSE endpoint using fetch streaming so the
 * Bearer token stays in the header (EventSource cannot set headers).
 * Returns an abort handle.
 */
export function streamLiveFeed(onEvent: (event: Record<string, unknown>) => void): () => void {
  const controller = new AbortController();

  (async () => {
    const access = getAccessToken();
    if (!access) return;
    try {
      const response = await fetch(`${API_BASE}/dashboard/stream`, {
        headers: { Authorization: `Bearer ${access}`, Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          for (const line of chunk.split("\n")) {
            if (line.startsWith("data: ")) {
              try {
                onEvent(JSON.parse(line.slice(6)));
              } catch {
                /* ignore malformed frame */
              }
            }
          }
        }
      }
    } catch {
      /* aborted or connection lost; caller re-subscribes via useEffect */
    }
  })();

  return () => controller.abort();
}
