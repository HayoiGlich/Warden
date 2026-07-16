import { NavLink, Link } from "react-router-dom";
import { hasPerm, roleLabel } from "../lib/perms";

const NAV_ITEMS = [
  { to: "/", icon: "bi-grid-1x2-fill", label: "Главная", end: true },
  { to: "/winlog", icon: "bi-shield-check", label: "Журналы" },
  { to: "/ad-users", icon: "bi-person-badge", label: "Пользователи AD" },
  { to: "/services", icon: "bi-grid-3x3-gap", label: "Сервисы" }
];

const ADMIN_NAV_ITEMS = [
  { to: "/yc-report", icon: "bi-hdd-stack", label: "Отчёт по ВМ", perm: "settings" },
  { to: "/settings", icon: "bi-gear", label: "Настройки", perm: "settings" }
];

export default function AppShell({ children, status, user, onLogout }) {
  const statusClass = status?.tone ? `is-${status.tone}` : "";
  const navItems = [
    ...NAV_ITEMS,
    ...ADMIN_NAV_ITEMS.filter((item) => hasPerm(user, item.perm))
  ];

  return (
    <div className="app-shell">
      <aside className="side-nav">
        <Link to="/" className="side-brand" aria-label="На главную">
          <span className="side-brand-mark" aria-hidden="true">M</span>
          <span className="side-brand-name">MID</span>
        </Link>

        <nav className="side-links" role="navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) => `side-link${isActive ? " is-active" : ""}`}
            >
              <i className={`bi ${item.icon}`} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="side-foot">
          {status ? (
            <span className={`side-status ${statusClass}`} title={status.title || ""}>
              <span className="dot" />
              <span className="side-status-label">{status.label}</span>
            </span>
          ) : null}

          {user ? (
            <div className="side-user" title={`${user.username} (${user.source})`}>
              <span className="side-user-avatar" aria-hidden="true">
                {String(user.username || "?").charAt(0).toUpperCase()}
              </span>
              <span className="side-user-main">
                <span className="side-user-name">
                  {user.display_name || user.username}
                </span>
                <span className={`side-user-role is-${user.role || "viewer"}`}>
                  {roleLabel(user)}
                </span>
              </span>
              <button
                type="button"
                className="side-logout"
                onClick={onLogout}
                title="Выйти"
                aria-label="Выйти"
              >
                <i className="bi bi-box-arrow-right" />
              </button>
            </div>
          ) : null}
        </div>
      </aside>

      <main className="app-main">{children}</main>
    </div>
  );
}
