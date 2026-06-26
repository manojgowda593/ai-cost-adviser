// Report rendering: summary, estimated savings, and a list of issues with
// severity badges + copyable AWS CLI fix commands. Exported as a reusable
// <ReportView> so both the Dashboard (live result) and History (saved result)
// render identically. The default export is a standalone page that reads the
// analysis passed via router location state.

import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Analysis, AnalyzeResult, Issue } from "../api";

function severityBadge(sev?: Issue["severity"]) {
  const map: Record<string, string> = {
    high: "bg-red-500/15 text-red-300 border-red-500/30",
    medium: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    low: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  };
  const cls = map[sev ?? "low"] ?? map.low;
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${cls} uppercase`}>
      {sev ?? "low"}
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable */
        }
      }}
      className="text-xs px-2 py-1 rounded bg-ink-700 hover:bg-ink-600 text-gray-200"
    >
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

export function ReportView({
  analysis,
  scannedServices,
  errors,
}: {
  analysis: Analysis;
  scannedServices?: string[];
  errors?: { service: string; error: string; hint?: string }[];
}) {
  const issues = analysis.issues ?? [];
  return (
    <div className="space-y-6">
      {/* Summary + savings */}
      <div className="grid gap-4 md:grid-cols-3">
        <div className="md:col-span-2 rounded-lg border border-ink-700 bg-ink-800 p-5">
          <h2 className="text-sm font-semibold text-gray-400 mb-2">Summary</h2>
          <p className="text-gray-100 text-sm leading-relaxed">{analysis.summary}</p>
          {scannedServices && scannedServices.length > 0 && (
            <p className="mt-3 text-xs text-gray-500">
              Scanned: {scannedServices.join(", ")}
            </p>
          )}
        </div>
        <div className="rounded-lg border border-ink-700 bg-ink-800 p-5 flex flex-col justify-center">
          <h2 className="text-sm font-semibold text-gray-400 mb-1">Estimated savings</h2>
          <p className="text-3xl font-bold text-green-400">
            ${Number(analysis.total_estimated_savings_usd ?? 0).toLocaleString()}
          </p>
          <p className="text-xs text-gray-500 mt-1">{issues.length} issue(s) found</p>
        </div>
      </div>

      {/* Scan errors, if any */}
      {errors && errors.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
          <h3 className="text-sm font-semibold text-amber-300 mb-2">Some services could not be scanned</h3>
          <ul className="space-y-1 text-sm text-amber-200/80">
            {errors.map((e, i) => (
              <li key={i}>
                <span className="font-medium">{e.service}:</span> {e.error}
                {e.hint && <span className="text-amber-200/50"> — {e.hint}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Issues */}
      <div className="space-y-3">
        {issues.length === 0 && (
          <div className="rounded-lg border border-ink-700 bg-ink-800 p-5 text-sm text-gray-400">
            No cost issues were found. 🎉
          </div>
        )}
        {issues.map((issue, i) => (
          <div key={i} className="rounded-lg border border-ink-700 bg-ink-800 p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  {severityBadge(issue.severity)}
                  {issue.service && (
                    <span className="text-xs text-gray-500">{issue.service}</span>
                  )}
                  {issue.resource_id && (
                    <span className="text-xs text-gray-600 font-mono">{issue.resource_id}</span>
                  )}
                </div>
                <h3 className="mt-2 text-gray-100 font-medium">{issue.issue}</h3>
                {issue.rationale && (
                  <p className="mt-1 text-sm text-gray-400">{issue.rationale}</p>
                )}
              </div>
              {issue.estimated_savings_usd != null && (
                <div className="text-right shrink-0">
                  <div className="text-green-400 font-semibold">
                    ${Number(issue.estimated_savings_usd).toLocaleString()}
                  </div>
                  <div className="text-xs text-gray-500">/mo est.</div>
                </div>
              )}
            </div>

            {issue.fix_command && (
              <div className="mt-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-500">Fix command (AWS CLI)</span>
                  <CopyButton text={issue.fix_command} />
                </div>
                <pre className="overflow-x-auto rounded-md bg-ink-900 border border-ink-700 p-3 text-xs text-sky-300 font-mono">
                  {issue.fix_command}
                </pre>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Report() {
  const location = useLocation();
  const navigate = useNavigate();
  const result = location.state as AnalyzeResult | undefined;

  if (!result) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 text-center">
        <p className="text-gray-400">No report to display.</p>
        <button onClick={() => navigate("/")} className="mt-4 text-accent hover:underline">
          Back to dashboard
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <button onClick={() => navigate(-1)} className="text-sm text-gray-400 hover:text-white mb-4">
        ← Back
      </button>
      <h1 className="text-2xl font-bold text-white mb-6">Analysis Report</h1>
      <ReportView
        analysis={result.analysis}
        scannedServices={result.scanned_services}
        errors={result.errors}
      />
    </div>
  );
}
