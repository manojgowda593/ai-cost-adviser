// Dashboard (step ② in the AWS flow): pick AWS services to scan + a region,
// run the analysis, watch live progress over WebSocket, then see the report
// inline. Replaces the Azure reference's "select a Resource Group" dropdown
// with multi-service selection (your chosen scope model) + a region picker.

import { useEffect, useState } from "react";
import { api, ApiError, type AnalyzeResult, type ServiceInfo } from "../api";
import ProgressTracker from "../components/ProgressTracker";
import { ReportView } from "./Report";

// Common AWS regions for the dropdown.
const REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "ap-south-1",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-northeast-1",
  "eu-west-1",
  "eu-west-2",
  "eu-central-1",
];

export default function Dashboard() {
  const [services, setServices] = useState<ServiceInfo[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [region, setRegion] = useState("us-east-1");
  const [search, setSearch] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .services()
      .then((r) => setServices(r.services))
      .catch((e) => setLoadErr(e instanceof ApiError ? e.message : "Failed to load services."));
  }, []);

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  function selectKeys(keys: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (on ? next.add(k) : next.delete(k)));
      return next;
    });
  }

  function clearAll() {
    setSelected(new Set());
  }

  async function runAnalysis() {
    if (selected.size === 0) {
      setRunErr("Select at least one service to scan.");
      return;
    }
    setRunErr(null);
    setResult(null);
    setRunning(true);
    // A temporary client-side id lets the ProgressTracker mount immediately.
    // The backend's real id arrives with the result; if they differ the tracker
    // simply shows the backlog the backend buffered. (Single-user dev flow.)
    setAnalysisId(`pending-${Date.now()}`);
    try {
      const res = await api.analyze(Array.from(selected), region);
      setResult(res);
      setAnalysisId(res.analysis_id); // re-point tracker to the real channel
    } catch (e) {
      setRunErr(e instanceof ApiError ? `${e.message}${e.hint ? ` — ${e.hint}` : ""}` : "Analysis failed.");
      setAnalysisId(null);
    } finally {
      setRunning(false);
    }
  }

  // Filter by the search box (matches label or category), then group by category.
  const q = search.trim().toLowerCase();
  const filtered = q
    ? services.filter(
        (s) => s.label.toLowerCase().includes(q) || s.category.toLowerCase().includes(q)
      )
    : services;
  const byCategory = filtered.reduce<Record<string, ServiceInfo[]>>((acc, s) => {
    (acc[s.category] ??= []).push(s);
    return acc;
  }, {});

  return (
    <div>
      {/* Page header */}
      <header className="border-b border-line bg-surface/80 px-5 py-4 backdrop-blur md:px-8 md:py-5">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-ink">Dashboard</h1>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-soft px-2.5 py-1 text-[11px] font-semibold text-brand">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
            </svg>
            AI agent
          </span>
        </div>
        <p className="mt-1 text-sm text-ink-muted">
          Choose what to inspect — the AI agent scans each resource and finds where you can cut cost.
        </p>
      </header>

      <div className="mx-auto max-w-5xl px-5 py-6 md:px-8 md:py-8">
        {loadErr && (
          <div className="mb-4 rounded-lg border border-sev-high/30 bg-sev-highSoft px-3 py-2 text-sm text-sev-high">
            {loadErr}
          </div>
        )}

        <div className="card p-6">
          {/* Region */}
          <div className="mb-6 max-w-xs">
            <label className="label mb-1.5 block">Region</label>
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none"
            >
              {REGIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>

          {/* Services: search + select controls */}
          <div className="mb-3 flex items-center justify-between">
            <label className="label">Services to scan</label>
            <div className="flex items-center gap-4 text-xs font-medium">
              <button
                type="button"
                onClick={() => selectKeys(filtered.map((s) => s.key), true)}
                className="text-brand hover:underline"
              >
                Select all{q ? " (filtered)" : ""}
              </button>
              <button type="button" onClick={clearAll} className="text-ink-muted hover:text-ink">
                Clear all
              </button>
            </div>
          </div>

          <div className="relative mb-5">
            <svg
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
              width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" strokeLinecap="round" />
            </svg>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search services — e.g. ec2, storage, lambda"
              className="w-full rounded-lg border border-line bg-canvas py-2.5 pl-9 pr-3 text-sm text-ink placeholder:text-ink-muted focus:border-brand focus:bg-surface focus:outline-none"
            />
          </div>

          <div className="grid gap-5 sm:grid-cols-3">
            {Object.entries(byCategory).map(([cat, list]) => {
              const catKeys = list.map((s) => s.key);
              const allSelected = catKeys.every((k) => selected.has(k));
              return (
                <div key={cat}>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="label">{cat}</span>
                    <button
                      type="button"
                      onClick={() => selectKeys(catKeys, !allSelected)}
                      className="text-[11px] font-medium text-brand hover:underline"
                    >
                      {allSelected ? "Clear" : "All"}
                    </button>
                  </div>
                  <div className="flex flex-col gap-1">
                    {list.map((s) => {
                      const on = selected.has(s.key);
                      return (
                        <label
                          key={s.key}
                          className={`flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-sm transition-all ${
                            on
                              ? "border-brand/30 bg-brand-soft font-medium text-ink"
                              : "border-line bg-surface text-ink-soft hover:border-line2 hover:bg-surface2"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={() => toggle(s.key)}
                            className="h-4 w-4 rounded accent-brand"
                          />
                          {s.label}
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {services.length === 0 && !loadErr && (
              <div className="text-sm text-ink-muted">Loading services…</div>
            )}
            {services.length > 0 && filtered.length === 0 && (
              <div className="text-sm text-ink-muted sm:col-span-3">
                No services match “{search}”.
              </div>
            )}
          </div>

          <div className="mt-7 flex items-center gap-4 border-t border-line pt-5">
            <button
              onClick={runAnalysis}
              disabled={running}
              className="inline-flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-hover disabled:opacity-50"
            >
              {running ? (
                <>
                  <Spinner /> Analyzing…
                </>
              ) : (
                "Run analysis"
              )}
            </button>
            <span className="nums text-sm text-ink-muted">
              {selected.size} service{selected.size === 1 ? "" : "s"} selected
            </span>
          </div>

          {runErr && (
            <div className="mt-4 rounded-lg border border-sev-high/30 bg-sev-highSoft px-3 py-2 text-sm text-sev-high">
              {runErr}
            </div>
          )}
        </div>

        {/* Live progress */}
        <ProgressTracker analysisId={analysisId} done={!!result} />

        {/* Inline report once done */}
        {result && (
          <div className="mt-8">
            <h2 className="mb-4 text-lg font-semibold text-ink">Results</h2>
            <ReportView
              analysis={result.analysis}
              scannedServices={result.scanned_services}
              resourceCount={result.resource_count}
              errors={result.errors}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.3" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
