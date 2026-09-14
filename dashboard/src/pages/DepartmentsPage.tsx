import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { DepartmentList } from "../api/types";

export function DepartmentsPage() {
  const [list, setList] = useState<DepartmentList | null>(null);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.departments();
      setList(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load departments");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    document.title = "Departments · Attendance Tracker";
    void load();
  }, [load]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setError(null);
    setBusy(true);
    try {
      await api.createDepartment(name.trim());
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create department");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    setError(null);
    setPendingDelete(id);
    try {
      await api.deleteDepartment(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete department");
    } finally {
      setPendingDelete(null);
    }
  };

  const departments = list?.departments ?? [];

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Departments</h1>
          <p className="subtitle">
            {loading ? "Loading departments…" : `${list?.total ?? 0} departments`}
          </p>
        </div>
      </header>

      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>New department</h2>
        </div>
        <form className="form-row" onSubmit={create}>
          <label className="sr-only" htmlFor="dept-name">
            Department name
          </label>
          <input
            id="dept-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New department name"
            maxLength={255}
            required
          />
          <button type="submit" className="btn primary" disabled={busy || !name.trim()}>
            {busy ? "Creating…" : "Create"}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>All departments</h2>
        </div>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">Departments in your organization</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Members</th>
                <th scope="col">Created</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {loading &&
                Array.from({ length: 3 }).map((_, index) => (
                  <tr key={`skeleton-${index}`}>
                    <td colSpan={4}>
                      <div className="skeleton skeleton-row" />
                    </td>
                  </tr>
                ))}

              {!loading &&
                departments.map((dept) => (
                  <tr key={dept.id}>
                    <td>{dept.name}</td>
                    <td>{dept.user_count}</td>
                    <td>{new Date(dept.created_at).toLocaleDateString()}</td>
                    <td className="cell-actions">
                      {pendingDelete === dept.id ? (
                        <span className="confirm-inline">
                          <span className="confirm-text">Delete?</span>
                          <button
                            type="button"
                            className="btn small danger"
                            onClick={() => remove(dept.id)}
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="btn small"
                            onClick={() => setPendingDelete(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn small danger"
                          onClick={() => setPendingDelete(dept.id)}
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                ))}

              {!loading && departments.length === 0 && (
                <tr>
                  <td colSpan={4}>
                    <div className="empty-state">
                      <strong>No departments yet</strong>
                      <span className="muted">
                        Create one above to organise your people and filter attendance.
                      </span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
