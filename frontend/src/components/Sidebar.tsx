import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";

// DEV ONLY: preview without login (matches App's RequireAuth bypass).
const DEV_SKIP_AUTH = import.meta.env.VITE_DEV_SKIP_AUTH === "true";

// Left navigation rail. Holds the brand, the primary destinations, and the
// signed-in user at the bottom. Only shown when authenticated.
export default function Sidebar() {
  const { user, logout, isAuthenticated } = useAuth();
  const loc = useLocation();

  if (!isAuthenticated && !DEV_SKIP_AUTH) return null;

  const item = (to: string, label: string, icon: JSX.Element) => {
    const active = loc.pathname === to;
    return (
      <Link
        to={to}
        className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
          active
            ? "bg-brand-soft text-brand"
            : "text-ink-soft hover:bg-canvas hover:text-ink"
        }`}
      >
        <span className={active ? "text-brand" : "text-ink-muted"}>{icon}</span>
        {label}
      </Link>
    );
  };

  const brand = (
    <div className="flex items-center gap-2.5">
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-white">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" fill="currentColor" />
        </svg>
      </span>
      <div className="leading-tight">
        <div className="text-sm font-semibold text-ink">Cost Adviser</div>
        <div className="hidden text-[11px] text-ink-muted md:block">AWS optimization</div>
      </div>
    </div>
  );

  return (
    <aside
      className="flex shrink-0 flex-row items-center gap-2 border-b border-line bg-surface px-4 py-3
                 md:w-60 md:flex-col md:items-stretch md:gap-0 md:border-b-0 md:border-r md:px-0 md:py-0"
    >
      {/* Brand */}
      <div className="md:px-5 md:py-5">{brand}</div>

      {/* Nav — horizontal on mobile, vertical column on desktop */}
      <nav className="flex flex-1 flex-row justify-end gap-1 md:flex-col md:justify-start md:px-3">
        {item("/", "Dashboard", <IconGrid />)}
        {item("/history", "History", <IconClock />)}
      </nav>

      {/* User — compact on mobile, full block at the bottom on desktop */}
      <div className="md:border-t md:border-line md:p-3">
        <div className="mb-0 hidden items-center gap-2.5 px-2 md:mb-2 md:flex">
          <span className="grid h-8 w-8 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-brand">
            {(user?.email ?? "?").slice(0, 1).toUpperCase()}
          </span>
          <span className="truncate text-xs text-ink-soft" title={user?.email}>
            {user?.email}
          </span>
        </div>
        <button
          onClick={logout}
          className="rounded-lg px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-canvas hover:text-ink md:w-full md:text-left"
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}

function IconGrid() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}

function IconClock() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
