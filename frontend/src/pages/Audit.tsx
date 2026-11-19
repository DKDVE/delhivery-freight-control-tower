import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { Card, CardHeader, DataTable, Drawer, ErrorState, Pager, Pill, Skeleton } from "@/components/ui";
import { ApiError, get, qs } from "@/lib/api";
import { cap, humanise, istTime, num, relTime } from "@/lib/fmt";
import { useParams2 } from "@/lib/hooks";
import type { AuditDetail, AuditRow, Page } from "@/lib/types";

const input = "h-9 rounded-md border border-[#CBD0D6] bg-surface px-2.5 text-sm";
const SCREENS = ["Booking check", "Trip alert", "Ask the data", "Vendor profile"];

const When = ({ iso }: { iso: string | null }) => <time dateTime={iso ?? undefined} title={istTime(iso)}>{relTime(iso)}</time>;

function DecisionPill({ d }: { d: AuditRow["decision"] }) {
  return d ? <Pill tone={d === "rejected" ? "grey" : "low"}>{cap(d)}</Pill> : <span className="text-ink2">{"–"}</span>;
}

function RunDrawer({ id }: { id: string }) {
  const nav = useNavigate();
  const { sp } = useParams2();
  const q = useQuery({ queryKey: ["audit", "run", id], queryFn: () => get<AuditDetail>(`/api/audit/${id}`) });
  const r = q.data;
  return (
    <Drawer open onClose={() => nav({ pathname: "/audit", search: sp.toString() })} title={`Run ${id}`} kicker="Audit record">
      {q.isPending && <Skeleton className="h-64" />}
      {q.isError && <ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} onRetry={() => q.refetch()} />}
      {r && (
        <>
          <dl className="m-0 grid grid-cols-[110px_1fr] gap-y-1.5 text-[13px]">
            <dt className="text-ink2">Screen</dt><dd className="m-0">{r.page}</dd>
            <dt className="text-ink2">Time</dt><dd className="m-0">{istTime(r.ts)}</dd>
            <dt className="text-ink2">Mode</dt><dd className="m-0">{r.mode} {"·"} {r.model_used}</dd>
            <dt className="text-ink2">Action</dt><dd className="m-0">{r.recommended_action ? cap(r.recommended_action) : "–"}</dd>
            <dt className="text-ink2">Decision</dt><dd className="m-0">{r.decision ? `${cap(r.decision)} (${istTime(r.decided_at)})` : "None recorded"}</dd>
            <dt className="text-ink2">Guardrail</dt><dd className="m-0">{r.guardrail_events.length ? r.guardrail_events.map(humanise).join(", ") : "No events"}</dd>
          </dl>
          <section><h3 className="mb-1 text-sm font-bold">Inputs</h3><pre className="mono m-0 overflow-x-auto rounded-md border border-line bg-[#FBFBFC] p-3">{JSON.stringify(r.inputs, null, 2)}</pre></section>
          <section><h3 className="mb-1 text-sm font-bold">Tools called</h3>{r.tools_called.length ? <ol className="m-0 pl-4">{r.tools_called.map((t, i) => <li key={i} className="mono">{t.name}({JSON.stringify(t.args)})</li>)}</ol> : <p className="m-0 text-ink2">None.</p>}</section>
          {r.run_text && <section><h3 className="mb-1 text-sm font-bold">Text produced</h3><pre className="mono m-0 whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3">{r.run_text}</pre></section>}
          {r.final_text && <section><h3 className="mb-1 text-sm font-bold">Text as decided</h3><pre className="mono m-0 whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3">{r.final_text}</pre></section>}
        </>
      )}
    </Drawer>
  );
}

export default function Audit() {
  const { runId } = useParams();
  const nav = useNavigate();
  const { get: p, set, sp } = useParams2();
  const filters = { screen: p("screen"), decision: p("decision"), mode: p("mode"), q: p("q"), date_from: p("date_from"), date_to: p("date_to") };
  const params = { ...filters, page: Number(p("page", "1")), page_size: 25 };
  const q = useQuery({ queryKey: ["audit", params], queryFn: () => get<Page<AuditRow>>("/api/audit", params), placeholderData: (d) => d });
  const sel = (id: string, label: string, opts: string[], val: string, fn: (v: string) => void) => (
    <div className="flex flex-col gap-1"><label htmlFor={id} className="text-xs font-semibold">{label}</label>
      <select id={id} className={input} value={val} onChange={(e) => fn(e.target.value)}><option value="">All</option>{opts.map((o) => <option key={o} value={o}>{cap(o)}</option>)}</select></div>
  );
  return (
    <>
      <Card>
        <CardHeader title="Audit log" sub={q.data ? `${num(q.data.total)} runs` : undefined}
          right={<a className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs font-semibold hover:bg-bg" href={`/api/audit/export.csv${qs(filters)}`} download><Download size={14} aria-hidden /> Export CSV</a>} />
        <div className="flex flex-wrap items-end gap-3 border-b border-line px-4 py-3">
          {sel("a-screen", "Screen", SCREENS, p("screen"), (v) => set({ screen: v }))}
          {sel("a-dec", "Decision", ["approved", "rejected", "edited", "pending"], p("decision"), (v) => set({ decision: v }))}
          {sel("a-mode", "Mode", ["DEMO", "LIVE"], p("mode"), (v) => set({ mode: v }))}
          <div className="flex flex-col gap-1"><label htmlFor="a-from" className="text-xs font-semibold">From</label><input id="a-from" type="date" className={input} value={p("date_from")} onChange={(e) => set({ date_from: e.target.value })} /></div>
          <div className="flex flex-col gap-1"><label htmlFor="a-to" className="text-xs font-semibold">To</label><input id="a-to" type="date" className={input} value={p("date_to")} onChange={(e) => set({ date_to: e.target.value })} /></div>
          <div className="flex flex-col gap-1"><label htmlFor="a-q" className="text-xs font-semibold">Search</label><input id="a-q" className={input} placeholder="Run ID or text" value={p("q")} onChange={(e) => set({ q: e.target.value })} /></div>
        </div>
        {q.isError ? <ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} onRetry={() => q.refetch()} /> : (
          <>
            <DataTable caption="Audit log" loading={q.isPending} data={q.data?.items ?? []} getId={(r) => r.run_id} selectedId={runId}
              onRowClick={(r) => nav({ pathname: `/audit/${r.run_id}`, search: sp.toString() })} empty={{ title: "No runs recorded", hint: "Run a booking check or ask a question and it appears here." }}
              columns={[
                { header: "When", cell: ({ row }) => <When iso={row.original.ts} /> },
                { header: "Screen", cell: ({ row }) => row.original.page },
                { header: "Run", cell: ({ row }) => <span className="mono">{row.original.run_id}</span> },
                { header: "Action", cell: ({ row }) => row.original.recommended_action ? cap(row.original.recommended_action) : <span className="text-ink2">{"–"}</span> },
                { header: "Decision", cell: ({ row }) => <DecisionPill d={row.original.decision} /> },
                { header: "Mode", cell: ({ row }) => row.original.mode },
              ]} />
            {q.data && <Pager page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPage={(n) => set({ page: n }, false)} />}
          </>
        )}
      </Card>
      {runId && <RunDrawer id={runId} />}
    </>
  );
}
