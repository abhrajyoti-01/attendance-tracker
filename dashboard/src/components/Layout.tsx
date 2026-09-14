import { useEffect, useRef, useState } from "react";
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
  const toggleRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  // Drawer focus management: move focus in, trap Tab, close on Escape, restore.
  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = sidebarRef.current;
    const focusables = sidebar?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    focusables?.[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        toggleRef.current?.focus();
        return;
      }
      if (event.key !== "Tab" || !focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [menuOpen]);

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
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

      <aside
        className={menuOpen ? "sidebar open" : "sidebar"}
        id="primary-sidebar"
        ref={sidebarRef}
      >
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

      <div className="shell-main">
        <header className="topbar">
          <button
            type="button"
            className="menu-btn"
            ref={toggleRef}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            aria-controls="primary-sidebar"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span />
            <span />
            <span />
          </button>
          <span className="topbar-title">Attendance Tracker</span>
        </header>
        <main className="content" id="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
