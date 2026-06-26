// History (step ⑥ read side): past analyses for the authenticated user.
// Lists services scanned, date, issues found, and estimated savings. Clicking
// a row expands the full saved report inline.

import { useEffect, useState } from "react";
import { api, ApiError, type HistoryItem } from "../api";
import { ReportView } from "./Report";

export default function History() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<number | null>(null);

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
          const open = openId === it.id;
          const date = it.created_at ? new Date(it.created_at).toLocaleString() : "—";
          const analysis = it.analysis_result?.analysis;
          return (
            <div key={it.id} className="rounded-lg border border-ink-700 bg-ink-800">
              <button
                onClick={() => setOpenId(open ? null : it.id)}
                className="w-full flex items-center justify-between gap-4 p-4 text-left"
              >
                <div>
                  <div className="text-gray-100 font-medium">
                    {it.services_scanned || "—"}
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">{date}</div>
                </div>
                <div className="flex items-center gap-6 text-sm shrink-0">
                  <span className="text-gray-400">
                    {it.issues_found} issue(s)
                  </span>
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
                  <span className="text-gray-500">{open ? "▲" : "▼"}</span>
                </div>
              </button>

              {open && (
                <div className="border-t border-ink-700 p-4">
                  {analysis ? (
                    <ReportView
                      analysis={analysis}
                      errors={
                        (it.analysis_result?.errors as
                          | { service: string; error: string; hint?: string }[]
                          | undefined) ?? undefined
                      }
                    />
                  ) : (
                    <p className="text-sm text-gray-400">
                      No detailed report saved for this analysis.
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
