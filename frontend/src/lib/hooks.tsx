import { useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, post } from "./api";
import { cap } from "./fmt";
import { ErrorState } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/overlays";

/** Filters live in the URL query string so every view is linkable and survives reload. */
export function useParams2() {
  const [sp, setSp] = useSearchParams();
  const get = (k: string, d = "") => sp.get(k) ?? d;
  const set = useCallback(
    (patch: Record<string, string | number | null | undefined>, resetPage = true) => {
      setSp((prev) => {
        const n = new URLSearchParams(prev);
        Object.entries(patch).forEach(([k, v]) => (v === null || v === undefined || v === "" ? n.delete(k) : n.set(k, String(v))));
        if (resetPage && !("page" in patch)) n.delete("page");
        return n;
      }, { replace: true });
    },
    [setSp],
  );
  return { get, set, sp };
}

/** Shows `skeleton` while loading, an error with Retry on failure, and children(data) otherwise. */
export function QueryBoundary<T>({ query, skeleton, children }: { query: UseQueryResult<T>; skeleton: ReactNode; children: (d: T) => ReactNode }) {
  if (query.isPending) return <>{skeleton}</>;
  if (query.isError) return <ErrorState message={query.error instanceof ApiError ? query.error.message : "Unexpected error"} onRetry={() => query.refetch()} />;
  return <>{children(query.data)}</>;
}

export type Decision = "approved" | "rejected" | "edited";
const UNDO_MS = 5000;

/**
 * Optimistic Approve/Reject/Edit. The UI shows the decision at once and a toast offers Undo for 5 s;
 * the decision is written to the audit log only after that window (or when the person leaves the page).
 */
export function useDecision(runId: string | undefined) {
  const toast = useToast();
  const qc = useQueryClient();
  const [state, setState] = useState<{ decision: Decision; saved: boolean } | null>(null);
  const pending = useRef<{ run_id: string; decision: Decision; final_text?: string } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const flush = useCallback(async () => {
    const p = pending.current;
    if (!p) return;
    pending.current = null;
    window.clearTimeout(timer.current);
    try {
      await post("/api/decisions", p);
      setState((s) => (s ? { ...s, saved: true } : s));
      qc.invalidateQueries({ queryKey: ["audit"] });
    } catch (e) {
      setState(null);
      toast({ title: "Decision not saved", description: e instanceof ApiError ? e.message : "Please try again.", tone: "error" });
    }
  }, [qc, toast]);

  const undo = useCallback(() => {
    window.clearTimeout(timer.current);
    pending.current = null;
    setState(null);
  }, []);

  const decide = useCallback(
    (decision: Decision, final_text?: string) => {
      if (!runId) return;
      void flush();
      pending.current = { run_id: runId, decision, final_text };
      setState({ decision, saved: false });
      toast({ title: `${cap(decision)}. Saving to the audit log`, description: "Nothing was sent. You can undo for 5 seconds.", actionLabel: "Undo", onAction: undo, duration: UNDO_MS });
      timer.current = window.setTimeout(() => void flush(), UNDO_MS);
    },
    [runId, flush, undo, toast],
  );

  // a new run replaces the old one: write any decision still inside its undo window, then start clean
  useEffect(() => { void flush(); setState(null); }, [runId, flush]);
  useEffect(() => () => { void flush(); }, [flush]);
  return { state, decide, undo };
}
