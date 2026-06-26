import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ApiError } from "../api";

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    setLoading(true);
    try {
      await signup(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Signup failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-full flex items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-xl border border-ink-700 bg-ink-800 p-8 shadow-xl"
      >
        <h1 className="text-2xl font-bold text-white mb-1">Create account</h1>
        <p className="text-sm text-gray-400 mb-6">Start optimizing your AWS costs.</p>

        {error && (
          <div className="mb-4 rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        <label className="block text-sm text-gray-300 mb-1">Email</label>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full mb-4 rounded-md bg-ink-900 border border-ink-600 px-3 py-2 text-sm text-gray-100 focus:border-accent focus:outline-none"
          placeholder="you@example.com"
        />

        <label className="block text-sm text-gray-300 mb-1">Password</label>
        <input
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full mb-6 rounded-md bg-ink-900 border border-ink-600 px-3 py-2 text-sm text-gray-100 focus:border-accent focus:outline-none"
          placeholder="At least 6 characters"
        />

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-accent hover:bg-accent-hover py-2 text-sm font-semibold text-ink-900 disabled:opacity-50"
        >
          {loading ? "Creating…" : "Create account"}
        </button>

        <p className="mt-4 text-center text-sm text-gray-400">
          Already have an account?{" "}
          <Link to="/login" className="text-accent hover:underline">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
