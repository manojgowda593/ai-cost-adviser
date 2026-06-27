import type { ReactNode } from "react";

// Shared shell for Login/Signup: a left brand panel that tells the cost-savings
// story, and a right form panel. On small screens only the form shows.
export const authInputClass =
  "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:border-brand focus:outline-none";

export default function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <div className="grid min-h-full md:grid-cols-2">
      {/* Brand / value panel — warm tinted ground, calm and minimal */}
      <div className="relative hidden flex-col justify-between overflow-hidden border-r border-line bg-canvas p-12 text-ink md:flex">
        {/* faint dot-grid texture — quiet warm dots */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, rgba(15,27,50,0.05) 1px, transparent 0)",
            backgroundSize: "24px 24px",
          }}
        />
        {/* faint scattered dollar motif — the cost/savings subject, kept quiet */}
        <DollarField />
        <div className="relative flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-white">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
            </svg>
          </span>
          <span className="text-sm font-semibold text-ink">Cost Adviser</span>
        </div>

        <div className="relative">
          {/* dollar coin badge above the headline */}
          <span className="mb-6 inline-grid h-11 w-11 place-items-center rounded-full bg-savings-soft ring-1 ring-savings/20">
            <span className="text-xl font-bold text-savings-ink">$</span>
          </span>
          <h2 className="max-w-sm text-[28px] font-semibold leading-[1.2] text-ink" style={{ textWrap: "balance" }}>
            Find the dollars hiding in your AWS account.
          </h2>
          <p className="mt-4 max-w-sm text-[15px] leading-relaxed text-ink-soft">
            An AI agent inspects your resources and their real usage, then tells
            you exactly where to cut cost — with the command to do it.
          </p>
          <div className="mt-10 flex gap-10">
            <Stat value="16+" label="services scanned" />
            <Stat value="14-day" label="usage signals" />
            <Stat value="AWS CLI" label="ready fixes" />
          </div>
        </div>

        <p className="relative text-xs text-ink-muted">Read-only. We never change your resources.</p>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center bg-canvas px-6 py-12">
        <div className="w-full max-w-sm">
          {/* brand shown on small screens where the left panel is hidden */}
          <div className="mb-8 flex items-center gap-2.5 md:hidden">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-white">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
              </svg>
            </span>
            <span className="text-sm font-semibold text-ink">Cost Adviser</span>
          </div>
          <h1 className="text-2xl font-semibold text-ink" style={{ textWrap: "balance" }}>
            {title}
          </h1>
          <p className="mb-7 mt-1 text-sm text-ink-muted">{subtitle}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="nums text-lg font-semibold text-ink">{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</div>
    </div>
  );
}

// A faint field of scattered "$" glyphs — a quiet nod to the cost/savings
// subject without turning the panel into clip-art. Fixed positions (no random)
// so it renders identically every load.
function DollarField() {
  const marks = [
    { top: "12%", left: "70%", size: 56, rot: -12, op: 0.05 },
    { top: "34%", left: "85%", size: 30, rot: 8, op: 0.06 },
    { top: "58%", left: "62%", size: 80, rot: -6, op: 0.04 },
    { top: "76%", left: "82%", size: 40, rot: 14, op: 0.05 },
    { top: "88%", left: "55%", size: 26, rot: -10, op: 0.06 },
    { top: "22%", left: "48%", size: 22, rot: 6, op: 0.05 },
  ];
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      {marks.map((m, i) => (
        <span
          key={i}
          className="absolute font-bold text-savings"
          style={{
            top: m.top,
            left: m.left,
            fontSize: m.size,
            opacity: m.op,
            transform: `rotate(${m.rot}deg)`,
          }}
        >
          $
        </span>
      ))}
    </div>
  );
}
