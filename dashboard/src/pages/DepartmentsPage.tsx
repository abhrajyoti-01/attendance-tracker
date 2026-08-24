import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DepartmentList } from "../api/types";

export function DepartmentsPage() {
  const [list, setList] = useState<DepartmentList | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api
      .departments()
      .then(setList)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load departments"));
  };

  useEffect(load, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.createDepartment(name.trim());
      setName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create department");
    }
  };

  const remove = async (id: string) => {
    setError(null);
    try {
      await api.deleteDepartment(id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete department");
    }
  };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Departments</h1>
          <p className="subtitle">{list?.total ?? "…"} departments</p>
        </div>
      </header>
      {error && <div className="alert error">{error}</div>}

      <section className="panel">
        <div className="panel-head">
          <h2>New department</h2>
        </div>
        <form className="form-row" onSubmit={create}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New department name"
            required
          />
          <button type="submit" className="btn primary">
            Create
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>All departments</h2>
        </div>
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Members</th>
              <th>Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(list?.departments ?? []).map((dept) => (
              <tr key={dept.id}>
                <td>{dept.name}</td>
                <td>{dept.user_count}</td>
                <td>{new Date(dept.created_at).toLocaleDateString()}</td>
                <td>
                  <button type="button" className="btn small danger" onClick={() => remove(dept.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {!list?.departments.length && (
              <tr>
                <td colSpan={4}>
                  <div className="empty-state">No departments yet</div>
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
