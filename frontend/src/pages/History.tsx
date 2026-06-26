// History (step ⑥ read side): past analyses for the authenticated user.
// Lists services scanned, date, issues found, and estimated savings. Clicking
// a row opens the full Report page for that analysis (deep-linkable).

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type AnalyzeResult, type HistoryItem } from "../api";

// Map a saved history row into the AnalyzeResult shape the Report page renders.
function toResult(it: HistoryItem): AnalyzeResult {
  const analysis = it.analysis_result?.analysis ?? {
    summary: "No detailed report was saved for this analysis.",
    total_estimated_savings_usd: Number(it.estimated_savings ?? 0),
    issues: [],
  };
  return {
    analysis_id: String(it.id),
    region: null,
    scanned_services: it.services_scanned ? it.services_scanned.split(", ") : [],
    resource_count: it.resources_scanned,
    resources: [],
    errors:
      (it.analysis_result?.errors as
        | { service: string; error: string; hint?: string }[]
        | undefined) ?? [],
    analysis,
    status: it.status === "failed" ? "failed" : "complete",
  };
}

export default function History() {
  const navigate = useNavigate();
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .history()
      .then((r) => setItems(r.history))
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load history."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <h1 className="text-2xl font-bold text-white mb-6">History</h1>

      {loading && <p className="text-sm text-gray-400">Loading…</p>}
      {error && (
        <div className="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="text-sm text-gray-400">No analyses yet. Run one from the dashboard.</p>
      )}

      <div className="space-y-3">
        {items.map((it) => {
          const date = it.created_at ? new Date(it.created_at).toLocaleString() : "—";
          return (
            <button
              key={it.id}
              onClick={() => navigate("/report", { state: toResult(it) })}
              className="w-full flex items-center justify-between gap-4 p-4 text-left rounded-lg border border-ink-700 bg-ink-800 hover:border-accent/50 transition-colors"
            >
              <div>
                <div className="text-gray-100 font-medium">{it.services_scanned || "—"}</div>
                <div className="text-xs text-gray-500 mt-0.5">{date}</div>
              </div>
              <div className="flex items-center gap-6 text-sm shrink-0">
                <span className="text-gray-400">{it.issues_found} issue(s)</span>
                <span className="text-green-400 font-semibold">
                  ${Number(it.estimated_savings ?? 0).toLocaleString()}
                </span>
                <span
                  className={`text-xs px-2 py-0.5 rounded border ${
                    it.status === "complete"
                      ? "border-green-500/30 text-green-300"
                      : it.status === "failed"
                      ? "border-red-500/30 text-red-300"
                      : "border-gray-500/30 text-gray-400"
                  }`}
                >
                  {it.status}
                </span>
                <span className="text-gray-500">→</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
