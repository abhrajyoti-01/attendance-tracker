import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: "grid", end: true },
  { to: "/users", label: "Users", icon: "users", end: false },
  { to: "/departments", label: "Departments", icon: "dept", end: false },
  { to: "/settings", label: "Settings", icon: "gear", end: false },
];

export function Layout() {
  const { userName, role, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const links = NAV_ITEMS.filter((item) => isAdmin || item.to === "/");

  return (
    <div className="shell">
      {menuOpen && (
        <button
          type="button"
          className="backdrop"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <aside className={menuOpen ? "sidebar open" : "sidebar"}>
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <span className="name">
            Attendance
            <span>Tracker</span>
          </span>
        </div>

        <nav aria-label="Primary">
          <p className="nav-section">Manage</p>
          {links.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              <span className={`nav-icon ${item.icon}`} aria-hidden />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip">
            <strong>{userName}</strong>
            <span className="role-badge">{role}</span>
          </div>
          <button type="button" className="btn secondary small" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </aside>

      <div>
        <header className="topbar">
          <button
            type="button"
            className="menu-btn"
            aria-label="Toggle navigation"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span />
            <span />
            <span />
          </button>
          <span className="topbar-title">Attendance Tracker</span>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
