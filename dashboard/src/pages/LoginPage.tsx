import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const FEATURES = [
  "Real-time attendance feed with liveness anti-spoofing",
  "Per-organization isolation and role-based access",
  "Face registration with quality-gated embeddings",
];

export function LoginPage() {
  const { login, userId, ready, expiredNotice, clearExpiredNotice } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "Sign in · Attendance Tracker";
  }, []);

  if (ready && userId) {
    return <Navigate to="/" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    clearExpiredNotice();
    setBusy(true);
    try {
      await login({ email: email.trim(), password, organization_slug: orgSlug.trim() });
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <section className="login-brand">
        <span className="brand-mark" aria-hidden />
        <h2>
          Face recognition attendance,
          <br />
          built for scale.
        </h2>
        <p>
          Multi-tenant attendance tracking with kiosk-ready recognition, audit trails, and
          operational tooling out of the box.
        </p>
        <ul className="feature-list">
          {FEATURES.map((feature) => (
            <li key={feature}>{feature}</li>
          ))}
        </ul>
      </section>

      <section className="login-panel">
        <form className="login-card" onSubmit={handleSubmit} noValidate>
          <h1>Sign in</h1>
          <p className="lead">Use your organization credentials to continue.</p>

          {expiredNotice && (
            <div className="alert warn" role="status">
              Your session expired. Please sign in again.
            </div>
          )}

          <label htmlFor="org">Organization slug</label>
          <input
            id="org"
            name="organization"
            value={orgSlug}
            onChange={(e) => setOrgSlug(e.target.value)}
            placeholder="acme"
            autoComplete="organization"
            autoCapitalize="none"
            spellCheck={false}
            required
          />

          <label htmlFor="email">Email</label>
          <input
            id="email"
            name="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
          />

          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter your password"
            autoComplete="current-password"
            required
          />

          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}

          <button type="submit" className="btn primary block" disabled={busy} aria-busy={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <p className="login-footer">
            Access is scoped to your organization. Contact your administrator if you need an
            account or a password reset.
          </p>
        </form>
      </section>
    </div>
  );
}
