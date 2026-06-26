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

  // Group services by category for a tidy display.
  const byCategory = services.reduce<Record<string, ServiceInfo[]>>((acc, s) => {
    (acc[s.category] ??= []).push(s);
    return acc;
  }, {});

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <h1 className="text-2xl font-bold text-white mb-1">Dashboard</h1>
      <p className="text-sm text-gray-400 mb-6">
        Select the AWS services and region to analyze for cost optimization.
      </p>

      {loadErr && (
        <div className="mb-4 rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-sm text-red-300">
          {loadErr}
        </div>
      )}

      <div className="rounded-lg border border-ink-700 bg-ink-800 p-5">
        {/* Region */}
        <label className="block text-sm font-medium text-gray-300 mb-1">Region</label>
        <select
          value={region}
          onChange={(e) => setRegion(e.target.value)}
          className="mb-5 w-56 rounded-md bg-ink-900 border border-ink-600 px-3 py-2 text-sm text-gray-100 focus:border-accent focus:outline-none"
        >
          {REGIONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>

        {/* Services grouped by category */}
        <label className="block text-sm font-medium text-gray-300 mb-2">Services</label>
        <div className="grid gap-4 sm:grid-cols-3">
          {Object.entries(byCategory).map(([cat, list]) => (
            <div key={cat}>
              <div className="text-xs uppercase tracking-wide text-gray-500 mb-2">{cat}</div>
              <div className="space-y-2">
                {list.map((s) => (
                  <label key={s.key} className="flex items-center gap-2 text-sm text-gray-200 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selected.has(s.key)}
                      onChange={() => toggle(s.key)}
                      className="accent-accent"
                    />
                    {s.label}
                  </label>
                ))}
              </div>
            </div>
          ))}
          {services.length === 0 && !loadErr && (
            <div className="text-sm text-gray-500">Loading services…</div>
          )}
        </div>

        <div className="mt-6 flex items-center gap-3">
          <button
            onClick={runAnalysis}
            disabled={running}
            className="rounded-md bg-accent hover:bg-accent-hover px-4 py-2 text-sm font-semibold text-ink-900 disabled:opacity-50"
          >
            {running ? "Analyzing…" : "Run Analysis"}
          </button>
          <span className="text-xs text-gray-500">{selected.size} selected</span>
        </div>

        {runErr && (
          <div className="mt-4 rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-sm text-red-300">
            {runErr}
          </div>
        )}
      </div>

      {/* Live progress */}
      <ProgressTracker analysisId={analysisId} done={!!result} />

      {/* Inline report once done */}
      {result && (
        <div className="mt-8">
          <h2 className="text-xl font-bold text-white mb-4">Report</h2>
          <ReportView
            analysis={result.analysis}
            scannedServices={result.scanned_services}
            resourceCount={result.resource_count}
            errors={result.errors}
          />
        </div>
      )}
    </div>
  );
}
