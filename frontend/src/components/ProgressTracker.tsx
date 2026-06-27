// Live progress over WebSocket (step ④). Given an analysisId, connects to
// ws/progress/{id} and renders each stage message as it arrives. The backend
// buffers messages, so connecting slightly late still replays the backlog.

import { useEffect, useRef, useState } from "react";
import { progressWsUrl } from "../api";

interface Props {
  analysisId: string | null;
  done: boolean; // parent sets true once the analyze() call resolves
}

export default function ProgressTracker({ analysisId, done }: Props) {
  const [messages, setMessages] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!analysisId) return;
    setMessages([]);
    const ws = new WebSocket(progressWsUrl(analysisId));
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.message) {
          setMessages((prev) =>
            prev[prev.length - 1] === data.message ? prev : [...prev, data.message]
          );
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [analysisId]);

  if (!analysisId) return null;

  return (
    <div className="card mt-6 p-5">
      <div className="mb-3 flex items-center gap-2">
        <span className="label">Progress</span>
        {!done && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />}
      </div>
      <ol className="flex flex-col gap-2.5">
        {messages.map((m, i) => {
          const isComplete = m === "Analysis complete";
          const isActive = i === messages.length - 1 && !done && !isComplete;
          return (
            <li key={i} className="flex items-center gap-3 text-sm">
              <span
                className={`grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[10px] ${
                  isComplete
                    ? "border-savings bg-savings text-white"
                    : isActive
                    ? "border-brand text-brand"
                    : "border-line bg-canvas text-ink-muted"
                }`}
              >
                {isComplete ? "✓" : isActive ? <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" /> : "•"}
              </span>
              <span className={isComplete ? "font-medium text-savings-ink" : "text-ink-soft"}>{m}</span>
            </li>
          );
        })}
        {messages.length === 0 && <li className="text-sm text-ink-muted">Connecting…</li>}
      </ol>
    </div>
  );
}
