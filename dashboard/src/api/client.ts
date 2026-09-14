/** Typed API client with access/refresh token rotation and SSE streaming. */

const API_BASE = "/api";
const ACCESS_KEY = "at.access_token";
const REFRESH_KEY = "at.refresh_token";
const REQUEST_TIMEOUT_MS = 30_000;

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
  fields: Record<string, string>;

  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fields = fields;
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

/**
 * FastAPI returns either a string or a list of validation-error objects for
 * `detail`. Stringifying the list produced the literal text "[object Object]".
 */
function describeDetail(detail: unknown, fallback: string): { message: string; fields: Record<string, string> } {
  if (typeof detail === "string" && detail.trim()) {
    return { message: detail, fields: {} };
  }
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    const parts: string[] = [];
    for (const item of detail) {
      if (!item || typeof item !== "object") continue;
      const entry = item as { loc?: unknown[]; msg?: string };
      const msg = typeof entry.msg === "string" ? entry.msg : "Invalid value";
      const loc = Array.isArray(entry.loc) ? entry.loc.filter((p) => p !== "body") : [];
      const field = loc.length ? loc.join(".") : "";
      if (field) fields[field] = msg;
      parts.push(field ? `${field}: ${msg}` : msg);
    }
    if (parts.length) return { message: parts.join("; "), fields };
  }
  return { message: fallback, fields: {} };
}

async function readError(response: Response): Promise<ApiError> {
  const fallback = `Request failed (${response.status})`;
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    return new ApiError(response.status, fallback);
  }
  const body = raw as { detail?: unknown; message?: unknown } | null;
  const detail = body?.detail ?? body?.message;
  const { message, fields } = describeDetail(detail, fallback);
  return new ApiError(response.status, message, fields);
}

async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const external = init.signal;
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", () => controller.abort(), { once: true });
  }
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

/**
 * Refresh is single-flight: several concurrent 401s must present the *same*
 * refresh token exactly once. The backend rotates on every use and treats a
 * replayed token as theft, revoking every session for the user.
 */
let refreshInFlight: Promise<boolean> | null = null;

export function refreshTokens(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refresh = getRefreshToken();
    if (!refresh) return false;
    try {
      const response = await fetchWithTimeout(`${API_BASE}/auth/refresh`, {
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
    } catch {
      // Network failure is not proof the session is gone; keep the tokens and
      // let the caller surface a retryable error instead of logging out.
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
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
    response = await fetchWithTimeout(`${API_BASE}${path}`, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, "The request timed out. Please try again.");
    }
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }

  if (response.status === 401 && retry && getRefreshToken()) {
    if (await refreshTokens()) {
      return request<T>(path, init, false);
    }
    window.dispatchEvent(new CustomEvent("at:session-expired"));
  }

  if (!response.ok) {
    throw await readError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/* ---------- typed endpoint helpers ---------- */

export interface LoginInput {
  email: string;
  password: string;
  organization_slug: string;
}

export const api = {
  login: (input: LoginInput) =>
    request<TokenResponse>("/auth/login", { method: "POST", body: JSON.stringify(input) }),

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

  users: (params: { page?: number; pageSize?: number; search?: string } = {}) => {
    const query = new URLSearchParams();
    query.set("page", String(params.page ?? 1));
    query.set("page_size", String(params.pageSize ?? 50));
    if (params.search?.trim()) query.set("search", params.search.trim());
    return request<import("./types").UserList>(`/users?${query.toString()}`);
  },
  createUser: (body: Record<string, unknown>) =>
    request("/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: string, body: Record<string, unknown>) =>
    request(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deactivateUser: (id: string) => request(`/users/${id}`, { method: "DELETE" }),

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

export type FeedStatus = "connecting" | "live" | "offline";

/**
 * Consume the admin live-feed SSE endpoint using fetch streaming so the bearer
 * token stays in the header (EventSource cannot set headers).
 *
 * Reconnects with exponential backoff and reports connection state so the UI
 * never claims to be streaming when it is not.
 */
export function streamLiveFeed(
  onEvent: (event: Record<string, unknown>) => void,
  onStatus?: (status: FeedStatus) => void,
): () => void {
  const controller = new AbortController();
  let stopped = false;
  let attempt = 0;

  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const timer = window.setTimeout(resolve, ms);
      controller.signal.addEventListener(
        "abort",
        () => {
          window.clearTimeout(timer);
          resolve();
        },
        { once: true },
      );
    });

  const run = async () => {
    while (!stopped && !controller.signal.aborted) {
      let access = getAccessToken();
      if (!access) return;

      onStatus?.("connecting");
      try {
        let response = await fetch(`${API_BASE}/dashboard/stream`, {
          headers: { Authorization: `Bearer ${access}`, Accept: "text/event-stream" },
          signal: controller.signal,
        });

        // The stream bypasses request(); route an expired token through the
        // shared single-flight refresh so it does not silently 401 forever.
        if (response.status === 401 && getRefreshToken()) {
          const refreshed = await refreshTokens();
          if (stopped || controller.signal.aborted) return;
          if (refreshed) {
            access = getAccessToken() ?? "";
            response = await fetch(`${API_BASE}/dashboard/stream`, {
              headers: { Authorization: `Bearer ${access}`, Accept: "text/event-stream" },
              signal: controller.signal,
            });
          } else {
            window.dispatchEvent(new CustomEvent("at:session-expired"));
            onStatus?.("offline");
            return;
          }
        }

        if (!response.ok || !response.body) {
          throw new Error(`stream failed: ${response.status}`);
        }

        attempt = 0;
        onStatus?.("live");

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
        if (stopped || controller.signal.aborted) return;
      }

      if (stopped || controller.signal.aborted) return;
      onStatus?.("offline");
      attempt += 1;
      // 1s, 2s, 4s, 8s, capped at 15s.
      await sleep(Math.min(1000 * 2 ** (attempt - 1), 15_000));
    }
  };

  void run();

  return () => {
    stopped = true;
    controller.abort();
  };
}
