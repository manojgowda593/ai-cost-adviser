import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";

export default function Navbar() {
  const { user, logout, isAuthenticated } = useAuth();
  const loc = useLocation();

  if (!isAuthenticated) return null;

  const link = (to: string, label: string) => {
    const active = loc.pathname === to;
    return (
      <Link
        to={to}
        className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
          active ? "bg-ink-700 text-white" : "text-gray-300 hover:bg-ink-700 hover:text-white"
        }`}
      >
        {label}
      </Link>
    );
  };

  return (
    <nav className="border-b border-ink-700 bg-ink-800">
      <div className="mx-auto max-w-6xl px-4 flex items-center justify-between h-14">
        <div className="flex items-center gap-2">
          <span className="text-accent font-bold text-lg">⛁ AI Cost Adviser</span>
          <div className="ml-6 flex gap-1">
            {link("/", "Dashboard")}
            {link("/history", "History")}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">{user?.email}</span>
          <button
            onClick={logout}
            className="px-3 py-1.5 rounded-md text-sm bg-ink-700 hover:bg-ink-600 text-gray-200"
          >
            Log out
          </button>
        </div>
      </div>
    </nav>
  );
}
