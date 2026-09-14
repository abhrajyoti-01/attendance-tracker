import { useCallback, useEffect, useState } from "react";
import { api, streamLiveFeed } from "../api/client";
import type { FeedStatus } from "../api/client";
import type {
  DailyChart,
  DashboardSummary,
  DepartmentChart,
  FeedEvent,
  HourlyChart,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BarChart } from "../components/BarChart";

const DAYS_WINDOW = 30;
const FEED_LIMIT = 30;

function isValidFeedEvent(raw: Record<string, unknown>): raw is Record<string, unknown> & FeedEvent {
  return (
    typeof raw.id === "string" &&
    typeof raw.user_id === "string" &&
    typeof raw.timestamp === "string" &&
    typeof raw.is_spoof === "boolean"
  );
}

function normaliseEvent(raw: Record<string, unknown>): FeedEvent {
  return {
    id: String(raw.id),
    user_id: String(raw.user_id),
    user_name: typeof raw.user_name === "string" ? raw.user_name : "Unknown",
    user_external_id: typeof raw.user_external_id === "string" ? raw.user_external_id : null,
    timestamp: String(raw.timestamp),
    method: typeof raw.method === "string" ? raw.method : "auto",
    confidence: typeof raw.confidence === "number" ? raw.confidence : null,
    is_spoof: Boolean(raw.is_spoof),
    department_name: typeof raw.department_name === "string" ? raw.department_name : null,
  };
}

function formatClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function timeAgo(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return date.toLocaleDateString();
}

function DashboardSkeleton() {
  return (
    <>
      <div className="card-grid">
        {Array.from({ length: 6 }, (_, i) => (
          <div className="skeleton stat" key={i} />
        ))}
      </div>
      <div className="panel-row">
        <div className="skeleton panel" />
        <div className="skeleton panel" />
      </div>
    </>
  );
}

export function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [daily, setDaily] = useState<DailyChart | null>(null);
  const [hourly, setHourly] = useState<HourlyChart | null>(null);
  const [departments, setDepartments] = useState<DepartmentChart | null>(null);
  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const [feedStatus, setFeedStatus] = useState<FeedStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const { isAdmin, userName } = useAuth();

  useEffect(() => {
    document.title = "Overview · Attendance Tracker";
  }, []);

  const load = useCallback(async () => {
    setRefreshing(true);
    // Settle each request independently so one failure does not blank the page.
    const [summaryResult, dailyResult, hourlyResult, deptResult] = await Promise.allSettled([
      api.summary(),
      api.dailyChart(DAYS_WINDOW),
      api.hourlyChart(),
      api.departmentChart(),
    ]);

    if (summaryResult.status === "fulfilled") setSummary(summaryResult.value);
    if (dailyResult.status === "fulfilled") setDaily(dailyResult.value);
    if (hourlyResult.status === "fulfilled") setHourly(hourlyResult.value);
    if (deptResult.status === "fulfilled") setDepartments(deptResult.value);

    const failure =
      summaryResult.status === "rejected"
        ? summaryResult.reason
        : dailyResult.status === "rejected"
          ? dailyResult.reason
          : hourlyResult.status === "rejected"
            ? hourlyResult.reason
            : deptResult.status === "rejected"
              ? deptResult.reason
              : null;

    setError(
      failure ? (failure instanceof Error ? failure.message : "Some dashboard data failed to load") : null,
    );
    setLastUpdated(new Date());
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Backfill the feed so the panel is not empty until the next live event.
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    api
      .feed(FEED_LIMIT)
      .then((data) => {
        if (!cancelled && data.events.length) setFeed(data.events);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);

  const onEvent = useCallback((raw: Record<string, unknown>) => {
    if (!isValidFeedEvent(raw)) return;
    const event = normaliseEvent(raw);
    setFeed((prev) => [event, ...prev.filter((e) => e.id !== event.id)].slice(0, FEED_LIMIT));
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    const abort = streamLiveFeed(onEvent, setFeedStatus);
    return abort;
  }, [isAdmin, onEvent]);

  const head = (
    <header className="page-head">
      <div>
        <h1>Overview</h1>
        <p className="subtitle">
          Welcome back{userName ? `, ${userName}` : ""}. Here is today at a glance.
          {lastUpdated && <span className="muted"> · updated {timeAgo(lastUpdated.toISOString())}</span>}
        </p>
      </div>
      <button
        type="button"
        className="btn secondary"
        onClick={() => void load()}
        disabled={refreshing}
        aria-busy={refreshing}
      >
        {refreshing ? "Refreshing…" : "Refresh"}
      </button>
    </header>
  );

  if (!summary) {
    return (
      <>
        {head}
        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}
        <DashboardSkeleton />
      </>
    );
  }

  return (
    <>
      {head}
      {error && (
        <div className="alert warn" role="alert">
          {error}
        </div>
      )}
      <DashboardLoaded
        summary={summary}
        daily={daily}
        hourly={hourly}
        departments={departments}
        feed={feed}
        feedStatus={feedStatus}
        isAdmin={isAdmin}
      />
    </>
  );
}

interface LoadedProps {
  summary: DashboardSummary;
  daily: DailyChart | null;
  hourly: HourlyChart | null;
  departments: DepartmentChart | null;
  feed: FeedEvent[];
  feedStatus: FeedStatus;
  isAdmin: boolean;
}

function DashboardLoaded({
  summary,
  daily,
  hourly,
  departments,
  feed,
  feedStatus,
  isAdmin,
}: LoadedProps) {
  const attendanceRate =
    summary.active_users > 0
      ? Math.round((summary.present_today / summary.active_users) * 100)
      : 0;

  const cards: Array<{ label: string; value: number; hint: string; tone?: string }> = [
    {
      label: "Present today",
      value: summary.present_today,
      hint: `${attendanceRate}% of active users`,
      tone: attendanceRate >= 80 ? "good" : attendanceRate >= 50 ? "warn" : "bad",
    },
    {
      label: "Absent today",
      value: summary.absent_today,
      hint: `${summary.active_users} active users`,
    },
    {
      label: "Active users",
      value: summary.active_users,
      hint: `${summary.total_users} total`,
    },
    {
      label: "Registered faces",
      value: summary.registered_users,
      hint: `${summary.unregistered_users} pending registration`,
    },
    {
      label: "Events today",
      value: summary.total_attendance_today,
      hint: "check-ins and manual marks",
    },
    {
      label: "Spoof attempts",
      value: summary.spoof_attempts_today,
      hint: "blocked by liveness checks",
      tone: summary.spoof_attempts_today > 0 ? "bad" : undefined,
    },
  ];

  return (
    <>
      <section className="card-grid" aria-label="Key metrics">
        {cards.map((card) => (
          <div className="stat-card" key={card.label}>
            <span className="stat-label">{card.label}</span>
            <span className={`stat-value ${card.tone ?? ""}`}>{card.value}</span>
            <span className="stat-hint">{card.hint}</span>
          </div>
        ))}
      </section>

      <section className="panel-row">
        <div className="panel">
          <div className="panel-head">
            <h2>Daily attendance</h2>
            <span className="hint">last {DAYS_WINDOW} days</span>
          </div>
          {daily && (
            <BarChart
              ariaLabel={`Daily attendance, unique present per day, last ${DAYS_WINDOW} days`}
              unitLabel="present"
              points={daily.data.map((d) => ({ label: d.date.slice(5), value: d.present }))}
            />
          )}
          <div className="chart-caption">
            <span className="legend">unique present per day</span>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>Check-ins by hour</h2>
            <span className="hint">today</span>
          </div>
          {hourly && (
            <BarChart
              ariaLabel="Check-ins by hour of day, today"
              unitLabel="check-ins"
              points={hourly.data.map((p) => ({
                label: `${String(p.hour).padStart(2, "0")}:00`,
                value: p.check_ins,
              }))}
            />
          )}
          <div className="chart-caption">
            <span className="legend">all 24 hours, local time</span>
          </div>
        </div>
      </section>

      <DepartmentsAndFeed
        departments={departments}
        feed={feed}
        feedStatus={feedStatus}
        isAdmin={isAdmin}
      />
    </>
  );
}

function DepartmentsAndFeed({
  departments,
  feed,
  feedStatus,
  isAdmin,
}: {
  departments: DepartmentChart | null;
  feed: FeedEvent[];
  feedStatus: FeedStatus;
  isAdmin: boolean;
}) {
  const statusLabel =
    feedStatus === "live" ? "streaming" : feedStatus === "connecting" ? "connecting…" : "offline";

  return (
    <section className="panel-row">
      <div className="panel">
        <div className="panel-head">
          <h2>Departments</h2>
          <span className="hint">today</span>
        </div>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">Attendance rate by department today</caption>
            <thead>
              <tr>
                <th scope="col">Department</th>
                <th scope="col">Members</th>
                <th scope="col">Present</th>
                <th scope="col">Rate</th>
              </tr>
            </thead>
            <tbody>
              {(departments?.data ?? []).map((dept) => {
                const pct = Math.round(dept.attendance_rate * 100);
                const tone = pct >= 80 ? "ok" : pct >= 50 ? "warn" : "bad";
                return (
                  <tr key={dept.department_id}>
                    <td>{dept.department_name}</td>
                    <td>{dept.total_users}</td>
                    <td>{dept.present_today}</td>
                    <td>
                      <span className={`pill ${tone}`}>{pct}%</span>
                    </td>
                  </tr>
                );
              })}
              {!departments?.data.length && (
                <tr>
                  <td colSpan={4}>
                    <div className="empty-state">
                      <strong>No departments yet</strong>
                      <span className="muted">
                        Create one to start tracking teams and their attendance.
                      </span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>Live feed</h2>
          <span className="hint">
            {isAdmin ? (
              <>
                <span
                  className={`live-dot ${feedStatus}`}
                  aria-hidden
                />
                <span aria-live="polite">{statusLabel}</span>
              </>
            ) : (
              "admins only"
            )}
          </span>
        </div>
        {isAdmin ? (
          feed.length ? (
            <ul className="feed-list">
              {feed.map((event) => (
                <li key={event.id} className={event.is_spoof ? "feed-item spoof" : "feed-item"}>
                  <span className={`method-pill m-${event.method}`}>{event.method}</span>
                  <span className="feed-body">
                    <strong>{event.is_spoof ? "Spoof attempt blocked" : event.user_name}</strong>
                    <span className="feed-sub">
                      {[
                        !event.is_spoof &&
                          (event.department_name ?? event.user_external_id ?? "—"),
                        event.confidence != null
                          ? `${Math.round(event.confidence * 100)}% match`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </span>
                  <span className="feed-meta">
                    <time dateTime={event.timestamp}>{formatClock(event.timestamp)}</time>
                    <span className="ago">{timeAgo(event.timestamp)}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty-state">
              <strong>
                {feedStatus === "offline" ? "Live feed disconnected" : "Waiting for events"}
              </strong>
              <span className="muted">
                {feedStatus === "offline"
                  ? "Reconnecting automatically. Recent events will appear once the stream is back."
                  : "Recognition check-ins will appear here the moment they happen."}
              </span>
            </div>
          )
        ) : (
          <div className="empty-state">
            The live event stream is available to organization administrators.
          </div>
        )}
      </div>
    </section>
  );
}
