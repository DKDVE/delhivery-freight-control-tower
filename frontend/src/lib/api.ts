export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Q = Record<string, string | number | boolean | null | undefined>;

export function qs(params: Q = {}): string {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) u.set(k, String(v));
  });
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check that the backend is running.");
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : j.errors ? j.errors.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`).join("; ") : msg;
    } catch { /* keep default */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const get = <T,>(path: string, params?: Q) => api<T>(`${path}${qs(params)}`);
export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface AskColumn { name: string; label: string; role: "dimension" | "measure" | "time"; unit: string | null; decimals: number }
export interface ChartSpec { type: string; x: string | null; y: string[]; title: string; valid_types: string[] }
export interface AskResult { columns: AskColumn[]; rows: Record<string, unknown>[]; row_count: number; total_rows?: number; notes?: string[]; sql_equivalent: string; filters_applied: Record<string, unknown> }
export interface FollowupItem { query_id: string; params: Record<string, unknown>; question: string }

export type AskStructuredEvent =
  | { event: "analysis"; data: { query_id: string; params: Record<string, unknown>; interpretation: string; data_scope: string } }
  | { event: "result"; data: AskResult }
  | { event: "chart"; data: ChartSpec }
  | { event: "summary"; data: { text: string; source: "llm" | "template"; guardrail_status: "passed" | "replaced"; guardrail_events: string[]; mode: string } }
  | { event: "followups"; data: { items: FollowupItem[] } }
  | { event: "refusal"; data: { reason: string; closest: string[] } }
  | { event: "empty"; data: { filters_applied: Record<string, unknown>; query_id: string; notes?: string[] } }
  | { event: "done"; data: { run_id: string | null } }
  | { event: "error"; data: { message: string } };

async function streamSSE<E extends { event: string }>(path: string, body: unknown, onEvent: (e: E) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!res.ok || !res.body) {
    let msg = `Request failed (${res.status})`;
    try { const j = await res.json(); msg = j.errors?.[0]?.message ?? j.detail ?? msg; } catch { /* default */ }
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (event && data) onEvent({ event, data: JSON.parse(data) } as unknown as E);
    }
  }
}

export interface AskHistoryTurn { question: string; query_id: string | null; params: Record<string, unknown> }

/** Streams the answer-card pipeline: analysis -> result -> chart -> summary -> followups -> done. */
export const streamAskCard = (question: string, history: AskHistoryTurn[], onEvent: (e: AskStructuredEvent) => void, signal?: AbortSignal) =>
  streamSSE<AskStructuredEvent>("/api/ask", { question, history }, onEvent, signal);

export interface RerunResult { kind: "ok" | "empty" | "error"; result?: AskResult; chart?: ChartSpec; summary?: string; message?: string }
export const rerunAsk = (run_id: string, params: Record<string, unknown>) => post<RerunResult>("/api/ask/rerun", { run_id, params });
export const sendFeedback = (run_id: string, kind: "good" | "fix_it" | "request_review", reasons: string[] = [], note?: string) =>
  post<{ run_id: string; recorded: boolean }>("/api/ask/feedback", { run_id, kind, reasons, note });

