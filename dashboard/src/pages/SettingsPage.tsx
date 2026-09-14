import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { OrgSettings } from "../api/types";

const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

export function SettingsPage() {
  const [settings, setSettings] = useState<OrgSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    document.title = "Settings · Attendance Tracker";
    let cancelled = false;
    api
      .settings()
      .then((data) => {
        if (!cancelled) setSettings(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load settings");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Optimistic write with rollback if the server rejects it. */
  const patch = async (updates: Partial<OrgSettings>) => {
    if (!settings) return;
    const previous = settings;
    setError(null);
    setNotice(null);
    setSaving(true);
    setSettings({ ...settings, ...updates });
    try {
      const saved = await api.updateSettings(updates as Record<string, unknown>);
      setSettings(saved);
      setNotice("Settings saved");
    } catch (err) {
      setSettings(previous);
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <>
        <header className="page-head">
          <div>
            <h1>Organization settings</h1>
            <p className="subtitle">Loading configuration…</p>
          </div>
        </header>
        <section className="panel">
          <div className="skeleton skeleton-row" />
          <div className="skeleton skeleton-row" />
          <div className="skeleton skeleton-row" />
        </section>
      </>
    );
  }

  if (!settings) {
    return (
      <>
        <header className="page-head">
          <div>
            <h1>Organization settings</h1>
            <p className="subtitle">Recognition and attendance behavior for your organization</p>
          </div>
        </header>
        <div className="alert error" role="alert">
          {error ?? "Settings could not be loaded."}
        </div>
      </>
    );
  }

  const timesValid =
    TIME_PATTERN.test(settings.working_hours_start) &&
    TIME_PATTERN.test(settings.working_hours_end);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Organization settings</h1>
          <p className="subtitle">
            Recognition and attendance behavior for your whole organization
          </p>
        </div>
        {saving && <span className="pill">Saving…</span>}
      </header>

      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="alert success" role="status">
          {notice}
        </div>
      )}

      <section className="panel">
        <h2>Attendance window</h2>
        <div className="settings-grid">
          <label htmlFor="start">Working hours start</label>
          <input
            id="start"
            type="time"
            value={settings.working_hours_start}
            onChange={(e) =>
              setSettings({ ...settings, working_hours_start: e.target.value })
            }
            onBlur={() =>
              timesValid && patch({ working_hours_start: settings.working_hours_start })
            }
          />

          <label htmlFor="end">Working hours end</label>
          <input
            id="end"
            type="time"
            value={settings.working_hours_end}
            onChange={(e) => setSettings({ ...settings, working_hours_end: e.target.value })}
            onBlur={() => timesValid && patch({ working_hours_end: settings.working_hours_end })}
          />

          <label htmlFor="late">Allow late check-in</label>
          <input
            id="late"
            type="checkbox"
            checked={settings.allow_late_checkin}
            onChange={(e) => patch({ allow_late_checkin: e.target.checked })}
          />
        </div>
        {!timesValid && (
          <p className="field-error" role="alert">
            Working hours must be valid times before they can be saved.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Recognition &amp; anti-spoofing</h2>
        <div className="settings-grid">
          <label htmlFor="threshold">
            Recognition threshold ({settings.recognition_threshold.toFixed(2)})
          </label>
          <input
            id="threshold"
            type="range"
            min={0.3}
            max={0.9}
            step={0.05}
            value={settings.recognition_threshold}
            aria-valuetext={settings.recognition_threshold.toFixed(2)}
            onChange={(e) =>
              setSettings({ ...settings, recognition_threshold: Number(e.target.value) })
            }
            onMouseUp={() => patch({ recognition_threshold: settings.recognition_threshold })}
            onTouchEnd={() => patch({ recognition_threshold: settings.recognition_threshold })}
            onKeyUp={() => patch({ recognition_threshold: settings.recognition_threshold })}
            onBlur={() => patch({ recognition_threshold: settings.recognition_threshold })}
          />

          <label htmlFor="liveness">Require liveness check</label>
          <input
            id="liveness"
            type="checkbox"
            checked={settings.require_liveness_check}
            onChange={(e) => patch({ require_liveness_check: e.target.checked })}
          />

          <label htmlFor="liveness-threshold">
            Liveness threshold ({settings.liveness_threshold.toFixed(2)})
          </label>
          <input
            id="liveness-threshold"
            type="range"
            min={0.3}
            max={1}
            step={0.05}
            value={settings.liveness_threshold}
            aria-valuetext={settings.liveness_threshold.toFixed(2)}
            onChange={(e) =>
              setSettings({ ...settings, liveness_threshold: Number(e.target.value) })
            }
            onMouseUp={() => patch({ liveness_threshold: settings.liveness_threshold })}
            onTouchEnd={() => patch({ liveness_threshold: settings.liveness_threshold })}
            onKeyUp={() => patch({ liveness_threshold: settings.liveness_threshold })}
            onBlur={() => patch({ liveness_threshold: settings.liveness_threshold })}
          />
        </div>
        <p className="hint">
          Higher thresholds reject more spoof attempts but may also reject genuine matches.
          Changes apply to subsequent recognition requests.
        </p>
      </section>

      <section className="panel">
        <h2>Capacity</h2>
        <div className="settings-grid">
          <div className="muted">User capacity</div>
          <div>
            <strong>{settings.max_users.toLocaleString()}</strong> registered users allowed
          </div>
        </div>
      </section>
    </>
  );
}
