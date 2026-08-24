import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamLiveFeed } from "../api/client";
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

function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
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
  const [error, setError] = useState<string | null>(null);
  const { isAdmin, userName } = useAuth();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const results = await Promise.all([
          api.summary(),
          api.dailyChart(DAYS_WINDOW),
          api.hourlyChart(),
          api.departmentChart(),
        ]);
        if (cancelled) return;
        setSummary(results[0]);
        setDaily(results[1]);
        setHourly(results[2]);
        setDepartments(results[3]);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load dashboard");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onEvent = useCallback((event: Record<string, unknown>) => {
    setFeed((prev) => [event as unknown as FeedEvent, ...prev].slice(0, 30));
  }, []);
  const abortRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!isAdmin) return;
    abortRef.current = streamLiveFeed(onEvent);
    return () => abortRef.current?.();
  }, [isAdmin, onEvent]);

  const head = (
    <header className="page-head">
      <div>
        <h1>Overview</h1>
        <p className="subtitle">
          Welcome back{userName ? `, ${userName}` : ""}. Here is today at a glance.
        </p>
      </div>
    </header>
  );

  if (error) {
    return (
      <>
        {head}
        <div className="alert error">{error}</div>
      </>
    );
  }
  if (!summary) {
    return (
      <>
        {head}
        <DashboardSkeleton />
      </>
    );
  }

  return <DashboardLoaded
    summary={summary}
    daily={daily}
    hourly={hourly}
    departments={departments}
    feed={feed}
    isAdmin={isAdmin}
  />;
}

interface LoadedProps {
  summary: DashboardSummary;
  daily: DailyChart | null;
  hourly: HourlyChart | null;
  departments: DepartmentChart | null;
  feed: FeedEvent[];
  isAdmin: boolean;
}

function DashboardLoaded({ summary, daily, hourly, departments, feed, isAdmin }: LoadedProps) {
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
            <BarChart points={daily.data.map((d) => ({ label: d.date.slice(5), value: d.present }))} />
          )}
          <div style={{ marginTop: "0.6rem" }}>
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
              points={hourly.data
                .filter((p) => p.hour >= 6 && p.hour <= 21)
                .map((p) => ({ label: `${p.hour}:00`, value: p.check_ins }))}
            />
          )}
        </div>
      </section>

      <DepartmentsAndFeed departments={departments} feed={feed} isAdmin={isAdmin} />
    </>
  );
}

function DepartmentsAndFeed({
  departments,
  feed,
  isAdmin,
}: {
  departments: DepartmentChart | null;
  feed: FeedEvent[];
  isAdmin: boolean;
}) {
  return (
    <section className="panel-row">
      <div className="panel">
        <div className="panel-head">
          <h2>Departments</h2>
          <span className="hint">today</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Department</th>
                <th>Members</th>
                <th>Present</th>
                <th>Rate</th>
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
                      No departments yet. Create one to start tracking teams.
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
                <span className="live-dot" aria-hidden /> streaming
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
                        !event.is_spoof && (event.department_name ?? event.user_external_id ?? "—"),
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
            <div className="empty-state">Connected and waiting for recognition events…</div>
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
