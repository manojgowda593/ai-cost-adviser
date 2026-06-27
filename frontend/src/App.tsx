import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Sidebar from "./components/Sidebar";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Dashboard from "./pages/Dashboard";
import Report from "./pages/Report";
import History from "./pages/History";
import type { ReactNode } from "react";

// DEV ONLY: set VITE_DEV_SKIP_AUTH=true in .env to bypass login locally (no DB
// needed) so you can preview the UI. Never set in production — real auth applies.
const DEV_SKIP_AUTH = import.meta.env.VITE_DEV_SKIP_AUTH === "true";

// Authenticated pages render inside the sidebar layout; unauthenticated users
// are redirected to /login.
function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated && !DEV_SKIP_AUTH) return <Navigate to="/login" replace />;
  return (
    <div className="flex h-full flex-col md:flex-row">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Dashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/report"
        element={
          <RequireAuth>
            <Report />
          </RequireAuth>
        }
      />
      <Route
        path="/history"
        element={
          <RequireAuth>
            <History />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
