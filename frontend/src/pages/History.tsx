// History (step ⑥ read side): past analyses for the authenticated user.
// Lists services scanned, date, issues found, and estimated savings. Clicking
// a row opens the full Report page for that analysis.

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
      (it.analysis_result?.errors as { service: string; error: string; hint?: string }[] | undefined) ?? [],
    analysis,
    status: it.status === "failed" ? "failed" : "complete",
  };
}

function usd(n: unknown) {
  return `$${Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
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
    <div>
      <header className="border-b border-line bg-surface/80 px-5 py-4 backdrop-blur md:px-8 md:py-5">
        <h1 className="text-xl font-semibold text-ink">History</h1>
        <p className="mt-0.5 text-sm text-ink-muted">Past analyses, newest first. Select one to open the full report.</p>
      </header>

      <div className="mx-auto max-w-4xl px-5 py-6 md:px-8 md:py-8">
        {loading && <p className="text-sm text-ink-muted">Loading…</p>}
        {error && (
          <div className="rounded-lg border border-sev-high/30 bg-sev-highSoft px-3 py-2 text-sm text-sev-high">
            {error}
          </div>
        )}

        {!loading && !error && items.length === 0 && (
          <div className="card p-8 text-center">
            <p className="text-sm text-ink-soft">No analyses yet.</p>
            <button onClick={() => navigate("/")} className="mt-3 text-sm font-medium text-brand hover:underline">
              Run your first analysis →
            </button>
          </div>
        )}

        <div className="flex flex-col gap-2.5">
          {items.map((it) => {
            const date = it.created_at ? new Date(it.created_at).toLocaleString() : "—";
            return (
              <button
                key={it.id}
                onClick={() => navigate("/report", { state: toResult(it) })}
                className="card flex items-center justify-between gap-4 p-4 text-left transition-all hover:border-line2 hover:shadow-cardHover"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-ink">{it.services_scanned || "—"}</div>
                  <div className="mt-0.5 text-xs text-ink-muted">{date}</div>
                </div>
                <div className="flex shrink-0 items-center gap-3 text-sm sm:gap-5">
                  <span className="nums hidden text-ink-soft sm:inline">{it.issues_found} issues</span>
                  <span className="nums font-semibold text-savings-ink">{usd(it.estimated_savings)}</span>
                  <StatusPill status={it.status} />
                  <span className="text-ink-muted">→</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    complete: "bg-savings-soft text-savings-ink",
    failed: "bg-sev-highSoft text-sev-high",
  };
  const cls = map[status] ?? "bg-canvas text-ink-muted";
  return <span className={`rounded-md px-2 py-0.5 text-[11px] font-medium ${cls}`}>{status}</span>;
}
