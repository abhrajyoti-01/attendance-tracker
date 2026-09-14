import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { DepartmentList, UserList } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const PAGE_SIZE = 25;

export function UsersPage() {
  const { isSuperadmin } = useAuth();
  const [list, setList] = useState<UserList | null>(null);
  const [departments, setDepartments] = useState<DepartmentList | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingToggle, setPendingToggle] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [externalId, setExternalId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("member");
  const [departmentId, setDepartmentId] = useState("");

  const load = useCallback(
    async (targetPage: number, searchTerm: string) => {
      setLoading(true);
      try {
        const data = await api.users({ page: targetPage, pageSize: PAGE_SIZE, search: searchTerm });
        setList(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load users");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    document.title = "Users · Attendance Tracker";
  }, []);

  useEffect(() => {
    void load(page, query);
  }, [load, page, query]);

  useEffect(() => {
    api
      .departments()
      .then(setDepartments)
      .catch(() => undefined);
  }, []);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await api.createUser({
        name: name.trim(),
        external_id: externalId.trim() || null,
        email: email.trim() || null,
        password: password || null,
        role,
        department_id: departmentId || null,
      });
      setNotice(
        `User "${name.trim()}" created${password ? "" : " — no sign-in until a password is set"}`,
      );
      setName("");
      setExternalId("");
      setEmail("");
      setPassword("");
      setDepartmentId("");
      await load(page, query);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (id: string, isActive: boolean) => {
    setError(null);
    setNotice(null);
    setPendingToggle(id);
    try {
      await api.updateUser(id, { is_active: !isActive });
      await load(page, query);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    } finally {
      setPendingToggle(null);
    }
  };

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setQuery(search);
  };

  const totalPages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;
  const users = list?.users ?? [];

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Users</h1>
          <p className="subtitle">
            {loading ? "Loading people…" : `${list?.total ?? 0} people in your organization`}
          </p>
        </div>
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
        <div className="panel-head">
          <h2>Create user</h2>
          <span className="hint">Password is optional; without one the user cannot sign in yet.</span>
        </div>
        <form className="form-row" onSubmit={createUser}>
          <div className="field">
            <label className="sr-only" htmlFor="user-name">
              Full name
            </label>
            <input
              id="user-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Full name"
              maxLength={255}
              required
            />
          </div>
          <div className="field">
            <label className="sr-only" htmlFor="user-external">
              Employee ID
            </label>
            <input
              id="user-external"
              value={externalId}
              onChange={(e) => setExternalId(e.target.value)}
              placeholder="Employee ID (optional)"
              maxLength={100}
            />
          </div>
          <div className="field">
            <label className="sr-only" htmlFor="user-email">
              Email address
            </label>
            <input
              id="user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email (optional)"
              autoCapitalize="none"
              spellCheck={false}
            />
          </div>
          <div className="field">
            <label className="sr-only" htmlFor="user-password">
              Initial password
            </label>
            <input
              id="user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Initial password (optional)"
              minLength={8}
              maxLength={72}
              autoComplete="new-password"
            />
          </div>
          <div className="field">
            <label className="sr-only" htmlFor="user-role">
              Role
            </label>
            <select id="user-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">member</option>
              {isSuperadmin && <option value="org_admin">org_admin</option>}
              {isSuperadmin && <option value="superadmin">superadmin</option>}
            </select>
          </div>
          <div className="field">
            <label className="sr-only" htmlFor="user-department">
              Department
            </label>
            <select
              id="user-department"
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
            >
              <option value="">No department</option>
              {(departments?.departments ?? []).map((dept) => (
                <option key={dept.id} value={dept.id}>
                  {dept.name}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" className="btn primary" disabled={busy || !name.trim()}>
            {busy ? "Adding…" : "Add user"}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>All users</h2>
          <form className="search-row" onSubmit={onSearch} role="search">
            <label className="sr-only" htmlFor="user-search">
              Search users
            </label>
            <input
              id="user-search"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, email or ID…"
              maxLength={200}
            />
            <button type="submit" className="btn small">
              Search
            </button>
          </form>
        </div>

        <div className="table-scroll">
          <table>
            <caption className="sr-only">People in your organization</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col">External ID</th>
                <th scope="col">Department</th>
                <th scope="col">Role</th>
                <th scope="col">Face registered</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {loading &&
                Array.from({ length: 5 }).map((_, index) => (
                  <tr key={`skeleton-${index}`}>
                    <td colSpan={8}>
                      <div className="skeleton skeleton-row" />
                    </td>
                  </tr>
                ))}

              {!loading &&
                users.map((user) => (
                  <tr key={user.id}>
                    <td>{user.name}</td>
                    <td>{user.email ?? "—"}</td>
                    <td>{user.external_id ?? "—"}</td>
                    <td>{user.department_name ?? "—"}</td>
                    <td>
                      <span className={`pill role-${user.role}`}>{user.role}</span>
                    </td>
                    <td>
                      {user.is_registered
                        ? `${(100 * (user.registration_quality ?? 0)).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td>
                      <span className={`pill ${user.is_active ? "ok" : "off"}`}>
                        {user.is_active ? "active" : "inactive"}
                      </span>
                    </td>
                    <td className="cell-actions">
                      <button
                        type="button"
                        className="btn small"
                        disabled={pendingToggle === user.id}
                        onClick={() => toggleActive(user.id, user.is_active)}
                      >
                        {pendingToggle === user.id
                          ? "Saving…"
                          : user.is_active
                            ? "Deactivate"
                            : "Activate"}
                      </button>
                    </td>
                  </tr>
                ))}

              {!loading && users.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <div className="empty-state">
                      <strong>{query ? "No matching users" : "No users yet"}</strong>
                      <span className="muted">
                        {query
                          ? `Nothing matched "${query}". Try a different search.`
                          : "Add your first person above, then register their face."}
                      </span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="pager">
          <button
            type="button"
            className="btn secondary small"
            disabled={page <= 1 || loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            ← Prev
          </button>
          <span className="muted">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className="btn secondary small"
            disabled={page >= totalPages || loading}
            onClick={() => setPage((p) => p + 1)}
          >
            Next →
          </button>
        </div>
      </section>
    </>
  );
}
