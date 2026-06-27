// Report rendering: a summary card (resources scanned / issues / estimated
// savings) and a list of issues with severity chips + copyable AWS CLI fixes.
// Exported as a reusable <ReportView> so Dashboard (live) and History (saved)
// render identically.

import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Analysis, AnalyzeResult, Issue } from "../api";

function SeverityChip({ sev }: { sev?: Issue["severity"] }) {
  const map: Record<string, string> = {
    high: "bg-sev-highSoft text-sev-high",
    medium: "bg-sev-medSoft text-sev-med",
    low: "bg-sev-lowSoft text-sev-low",
  };
  const cls = map[sev ?? "low"] ?? map.low;
  return (
    <span className={`rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${cls}`}>
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
      className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink-soft transition-colors hover:bg-canvas"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function usd(n: unknown) {
  return `$${Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function ReportView({
  analysis,
  scannedServices,
  resourceCount,
  errors,
}: {
  analysis: Analysis;
  scannedServices?: string[];
  resourceCount?: number;
  errors?: { service: string; error: string; hint?: string }[];
}) {
  const issues = analysis.issues ?? [];
  const savings = Number(analysis.total_estimated_savings_usd ?? 0);

  return (
    <div className="flex flex-col gap-5">
      {/* Summary card — savings is the hero */}
      <div className="card overflow-hidden">
        <div className="grid sm:grid-cols-3">
          <Stat label="Resources scanned" value={resourceCount ?? "—"} />
          <Stat label="Issues found" value={issues.length} divider />
          <div className="bg-savings-soft p-5">
            <div className="label text-savings-ink/70">Est. monthly savings</div>
            <div className="nums mt-1 text-3xl font-semibold text-savings-ink">{usd(savings)}</div>
          </div>
        </div>
        <div className="border-t border-line p-5">
          <div className="label mb-1.5">Summary</div>
          <p className="text-sm leading-relaxed text-ink-soft">{analysis.summary}</p>
          {scannedServices && scannedServices.length > 0 && (
            <p className="mt-2 text-xs text-ink-muted">Scanned: {scannedServices.join(", ")}</p>
          )}
        </div>
      </div>

      {/* Scan errors, if any */}
      {errors && errors.length > 0 && (
        <div className="rounded-xl2 border border-sev-med/30 bg-sev-medSoft p-4">
          <div className="mb-2 text-sm font-semibold text-sev-med">Some services couldn’t be scanned</div>
          <ul className="flex flex-col gap-1 text-sm text-sev-med/90">
            {errors.map((e, i) => (
              <li key={i}>
                <span className="font-medium">{e.service}:</span> {e.error}
                {e.hint && <span className="text-sev-med/60"> — {e.hint}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Issues */}
      {issues.length === 0 ? (
        <div className="card flex items-center gap-3 p-6 text-sm text-ink-soft">
          <span className="grid h-8 w-8 place-items-center rounded-full bg-savings-soft text-savings">✓</span>
          No cost-saving issues found — these resources look efficient.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {issues.map((issue, i) => (
            <div key={i} className="card p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityChip sev={issue.severity} />
                    {issue.service && <span className="text-xs font-medium text-ink-soft">{issue.service}</span>}
                    {issue.resource_name && issue.resource_name !== issue.resource_id && (
                      <span className="text-xs font-medium text-ink">{issue.resource_name}</span>
                    )}
                    {issue.resource_id && (
                      <span className="nums text-xs text-ink-muted">{issue.resource_id}</span>
                    )}
                  </div>
                  <h3 className="mt-2 font-semibold text-ink" style={{ textWrap: "balance" }}>
                    {issue.issue}
                  </h3>
                </div>
                {issue.estimated_savings_usd != null && Number(issue.estimated_savings_usd) > 0 && (
                  <div className="shrink-0 text-right">
                    <div className="nums font-semibold text-savings-ink">
                      {usd(issue.estimated_savings_usd)}
                    </div>
                    <div className="text-[11px] text-ink-muted">/mo est.</div>
                  </div>
                )}
              </div>

              {/* Current state — the evidence/history */}
              {issue.current_state && (
                <div className="mt-3">
                  <div className="label mb-1">What we found</div>
                  <p className="text-sm leading-relaxed text-ink-soft">{issue.current_state}</p>
                </div>
              )}

              {/* Recommendation — the reasoned action */}
              {(issue.recommendation || issue.rationale) && (
                <div className="mt-3">
                  <div className="label mb-1">Recommendation</div>
                  <p className="text-sm leading-relaxed text-ink">
                    {issue.recommendation || issue.rationale}
                  </p>
                </div>
              )}

              {/* Safety caveat — surfaced prominently for destructive actions */}
              {(issue.caveats || issue.requires_data_check) && (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-sev-med/30 bg-sev-medSoft px-3 py-2">
                  <span className="mt-0.5 text-sev-med" aria-hidden="true">⚠</span>
                  <p className="text-xs leading-relaxed text-sev-med">
                    {issue.caveats ||
                      "This action can remove data — verify there's nothing critical (snapshot first if unsure) before applying."}
                  </p>
                </div>
              )}

              {issue.fix_command && (
                <div className="mt-4">
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="label">Fix · AWS CLI</span>
                    <CopyButton text={issue.fix_command} />
                  </div>
                  <pre className="overflow-x-auto rounded-lg border border-line bg-canvas p-3 text-xs leading-relaxed text-ink-soft">
                    <code className="font-mono">{issue.fix_command}</code>
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, divider }: { label: string; value: React.ReactNode; divider?: boolean }) {
  return (
    <div className={`p-5 ${divider ? "border-line sm:border-l" : ""}`}>
      <div className="label">{label}</div>
      <div className="nums mt-1 text-3xl font-semibold text-ink">{value}</div>
    </div>
  );
}

export default function Report() {
  const location = useLocation();
  const navigate = useNavigate();
  const result = location.state as AnalyzeResult | undefined;

  if (!result) {
    return (
      <div className="mx-auto max-w-3xl px-8 py-16 text-center">
        <p className="text-ink-muted">No report to display.</p>
        <button onClick={() => navigate("/")} className="mt-4 font-medium text-brand hover:underline">
          Back to dashboard
        </button>
      </div>
    );
  }

  return (
    <div>
      <header className="border-b border-line bg-surface/80 px-5 py-4 backdrop-blur md:px-8 md:py-5">
        <button onClick={() => navigate(-1)} className="mb-1 text-sm text-ink-muted hover:text-ink">
          ← Back
        </button>
        <h1 className="text-xl font-semibold text-ink">Analysis report</h1>
      </header>
      <div className="mx-auto max-w-4xl px-5 py-6 md:px-8 md:py-8">
        <ReportView
          analysis={result.analysis}
          scannedServices={result.scanned_services}
          resourceCount={result.resource_count}
          errors={result.errors}
        />
      </div>
    </div>
  );
}
