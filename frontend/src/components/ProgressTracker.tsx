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
    <div className="mt-6 rounded-lg border border-ink-700 bg-ink-800 p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Progress</h3>
      <ul className="space-y-2">
        {messages.map((m, i) => {
          const isComplete = m === "Analysis complete";
          const isLast = i === messages.length - 1;
          return (
            <li key={i} className="flex items-center gap-2 text-sm">
              <span
                className={
                  isComplete
                    ? "text-green-400"
                    : isLast && !done
                    ? "text-accent animate-pulse"
                    : "text-gray-400"
                }
              >
                {isComplete ? "✓" : isLast && !done ? "◐" : "•"}
              </span>
              <span className={isComplete ? "text-green-300" : "text-gray-200"}>{m}</span>
            </li>
          );
        })}
        {messages.length === 0 && (
          <li className="text-sm text-gray-500">Connecting…</li>
        )}
      </ul>
    </div>
  );
}
