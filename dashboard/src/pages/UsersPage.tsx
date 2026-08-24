import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DepartmentList, UserList } from "../api/types";

export function UsersPage() {
  const [list, setList] = useState<UserList | null>(null);
  const [departments, setDepartments] = useState<DepartmentList | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [externalId, setExternalId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("member");
  const [departmentId, setDepartmentId] = useState("");

  const load = (p: number) => {
    api
      .users(p)
      .then(setList)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load users"));
  };

  useEffect(() => {
    load(page);
    api.departments().then(setDepartments).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      await api.createUser({
        name,
        external_id: externalId || null,
        email: email || null,
        password: password || null,
        role,
        department_id: departmentId || null,
      });
      setNotice(`User "${name}" created${password ? "" : " (no sign-in until a password is set)"}`);
      setName("");
      setExternalId("");
      setEmail("");
      setPassword("");
      load(page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    }
  };

  const toggleActive = async (id: string, isActive: boolean) => {
    try {
      await api.updateUser(id, { is_active: !isActive });
      load(page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    }
  };

  const totalPages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Users</h1>
          <p className="subtitle">{list?.total ?? "…"} people in your organization</p>
        </div>
      </header>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert success">{notice}</div>}

      <section className="panel">
        <div className="panel-head">
          <h2>Create user</h2>
          <span className="hint">password is optional; without one the user cannot sign in yet</span>
        </div>
        <form className="form-row" onSubmit={createUser}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" required />
          <input
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
            placeholder="Employee ID (optional)"
          />
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email (optional)"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Initial password (optional)"
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">member</option>
            <option value="org_admin">org_admin</option>
          </select>
          <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
            <option value="">No department</option>
            {(departments?.departments ?? []).map((dept) => (
              <option key={dept.id} value={dept.id}>
                {dept.name}
              </option>
            ))}
          </select>
          <button type="submit" className="btn primary">
            Add
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>All users</h2>
          <span className="hint">page {page} of {totalPages}</span>
        </div>
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>External ID</th>
              <th>Department</th>
              <th>Role</th>
              <th>Face registered</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(list?.users ?? []).map((user) => (
              <tr key={user.id}>
                <td>{user.name}</td>
                <td>{user.email ?? "—"}</td>
                <td>{user.external_id ?? "—"}</td>
                <td>{user.department_name ?? "—"}</td>
                <td>{user.role}</td>
                <td>{user.is_registered ? `${(100 * (user.registration_quality ?? 0)).toFixed(0)}%` : "—"}</td>
                <td>
                  <span className={`pill ${user.is_active ? "ok" : "off"}`}>
                    {user.is_active ? "active" : "inactive"}
                  </span>
                </td>
                <td>
                  <button type="button" className="btn small" onClick={() => toggleActive(user.id, user.is_active)}>
                    {user.is_active ? "Deactivate" : "Activate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        <div className="pager">
          <button type="button" className="btn secondary small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            ← Prev
          </button>
          <span className="muted">{list?.total ?? 0} users</span>
          <button type="button" className="btn secondary small" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
            Next →
          </button>
        </div>
      </section>
    </>
  );
}
