// Central API client. Attaches the JWT from localStorage to every request as
// `Authorization: Bearer <token>` and normalizes the backend's error shape
// ({ detail: { message, hint } }) into a thrown Error.

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export const TOKEN_KEY = "aca_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  hint?: string;
  constructor(status: number, message: string, hint?: string) {
    super(message);
    this.status = status;
    this.hint = hint;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (!res.ok) {
    let message = res.statusText;
    let hint: string | undefined;
    try {
      const body = await res.json();
      // FastAPI HTTPException wraps our dict in `detail`.
      const detail = body?.detail ?? body;
      if (typeof detail === "string") message = detail;
      else {
        message = detail?.message ?? message;
        hint = detail?.hint;
      }
    } catch {
      /* non-JSON error body; keep statusText */
    }
    throw new ApiError(res.status, message, hint);
  }

  // 204 / empty bodies
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Types matching the backend responses ----
export interface User {
  id: number;
  email: string;
}

export interface AuthResponse {
  token: string;
  user: User;
}

export interface ServiceInfo {
  key: string;
  label: string;
  category: string;
}

export interface Issue {
  service?: string;
  category?: string;
  resource_id?: string;
  resource_name?: string;
  issue?: string;
  severity?: "high" | "medium" | "low";
  current_state?: string;
  recommendation?: string;
  estimated_savings_usd?: number;
  requires_data_check?: boolean;
  caveats?: string;
  fix_command?: string;
  rationale?: string; // legacy fallback
}

export interface Analysis {
  summary: string;
  total_estimated_savings_usd: number;
  issues: Issue[];
}

export interface AnalyzeResult {
  analysis_id: string;
  region: string | null;
  scanned_services: string[];
  resource_count: number;
  resources: unknown[];
  errors: { service: string; error: string; hint?: string }[];
  analysis: Analysis;
  status: "complete" | "failed";
}

export interface HistoryItem {
  id: number;
  user_id: number | null;
  services_scanned: string | null;
  resources_scanned: number;
  issues_found: number;
  estimated_savings: string | null;
  analysis_result: { analysis?: Analysis; errors?: unknown[] } | null;
  status: string;
  created_at: string | null;
}

// ---- Endpoints ----
export const api = {
  signup: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  services: () => request<{ services: ServiceInfo[] }>("/api/services"),

  analyze: (services: string[], region: string | null) =>
    request<AnalyzeResult>("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ services, region }),
    }),

  history: () => request<{ history: HistoryItem[] }>("/api/history"),
};

// WebSocket URL for live progress (derives ws:// from the API base).
export function progressWsUrl(analysisId: string): string {
  const wsBase = API_BASE.replace(/^http/, "ws");
  return `${wsBase}/ws/progress/${analysisId}`;
}
