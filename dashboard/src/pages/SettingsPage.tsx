import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { OrgSettings } from "../api/types";

export function SettingsPage() {
  const [settings, setSettings] = useState<OrgSettings | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load settings"));
  }, []);

  const patch = async (updates: Partial<OrgSettings>) => {
    if (!settings) return;
    setError(null);
    setNotice(null);
    const optimistic = { ...settings, ...updates };
    setSettings(optimistic);
    try {
      const saved = await api.updateSettings(updates as Record<string, unknown>);
      setSettings(saved);
      setNotice("Settings saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    }
  };

  if (!settings) return <p className="muted">Loading settings…</p>;

  const timeValid = /^([01]\d|2[0-3]):[0-5]\d$/.test(settings.working_hours_start) &&
    /^([01]\d|2[0-3]):[0-5]\d$/.test(settings.working_hours_end);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Organization settings</h1>
          <p className="subtitle">Recognition and attendance behavior for your whole organization</p>
        </div>
      </header>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert success">{notice}</div>}

      <section className="panel">
      <div className="settings-grid">
        <label htmlFor="start">Working hours start</label>
        <input
          id="start"
          value={settings.working_hours_start}
          onChange={(e) => setSettings({ ...settings, working_hours_start: e.target.value })}
          onBlur={() => timeValid && patch({ working_hours_start: settings.working_hours_start })}
        />

        <label htmlFor="end">Working hours end</label>
        <input
          id="end"
          value={settings.working_hours_end}
          onChange={(e) => setSettings({ ...settings, working_hours_end: e.target.value })}
          onBlur={() => timeValid && patch({ working_hours_end: settings.working_hours_end })}
        />

        <label htmlFor="late">Allow late check-in</label>
        <input
          id="late"
          type="checkbox"
          checked={settings.allow_late_checkin}
          onChange={(e) => patch({ allow_late_checkin: e.target.checked })}
        />

        <label htmlFor="threshold">Recognition threshold ({settings.recognition_threshold.toFixed(2)})</label>
        <input
          id="threshold"
          type="range"
          min={0.3}
          max={0.9}
          step={0.05}
          value={settings.recognition_threshold}
          onChange={(e) => setSettings({ ...settings, recognition_threshold: Number(e.target.value) })}
          onMouseUp={() => patch({ recognition_threshold: settings.recognition_threshold })}
          onTouchEnd={() => patch({ recognition_threshold: settings.recognition_threshold })}
        />

        <label htmlFor="liveness">Require liveness check</label>
        <input
          id="liveness"
          type="checkbox"
          checked={settings.require_liveness_check}
          onChange={(e) => patch({ require_liveness_check: e.target.checked })}
        />

        <div className="muted full-width">User capacity: {settings.max_users}</div>
      </div>
      </section>
    </>
  );
}
