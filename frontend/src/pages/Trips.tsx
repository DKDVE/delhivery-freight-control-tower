import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, DataTable, ErrorState, Pager, ProbabilityBar, RiskBadge, Tabs } from "@/components/ui";
import { ApiError, get } from "@/lib/api";
import { mins, num, prob } from "@/lib/fmt";
import { useParams2 } from "@/lib/hooks";
import type { TripPage } from "@/lib/types";

const input = "h-9 rounded-md border border-[#CBD0D6] bg-surface px-2.5 text-sm";

export default function Trips() {
  const nav = useNavigate();
  const { get: p, set, sp } = useParams2();
  const risk = p("risk", "high");
  const sort = p("sort", "severe_probability");
  const order = p("order", "desc") as "asc" | "desc";
  const page = Number(p("page", "1"));
  const params = { risk, q: p("q"), corridor: p("corridor"), sort, order, page, page_size: 25 };
  const q = useQuery({ queryKey: ["trips", params], queryFn: () => get<TripPage>("/api/trips", params), placeholderData: (d) => d });
  const onSort = (k: string) => set({ sort: k, order: sort === k && order === "desc" ? "asc" : "desc" });
  return (
    <Card>
      <CardHeader title="Trips" sub={q.data ? `${num(q.data.high_risk_total)} flagged as likely to run 2.5x plan` : undefined}
        right={<Tabs label="Risk filter" value={risk} onValueChange={(v) => set({ risk: v })} items={[{ value: "high", label: "High risk" }, { value: "all", label: "All trips" }]} />} />
      <div className="flex flex-wrap items-end gap-3 border-b border-line px-4 py-3">
        <div className="flex flex-col gap-1"><label htmlFor="tq" className="text-xs font-semibold">Trip or centre</label><input id="tq" className={input} placeholder="Trip ID or centre code" value={p("q")} onChange={(e) => set({ q: e.target.value })} /></div>
        <div className="flex flex-col gap-1"><label htmlFor="tc" className="text-xs font-semibold">Corridor</label><input id="tc" className={input} placeholder="e.g. Delhi" value={p("corridor")} onChange={(e) => set({ corridor: e.target.value })} /></div>
      </div>
      {q.isError ? <ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} onRetry={() => q.refetch()} /> : (
        <>
          <DataTable
            caption="Trips" loading={q.isPending} data={q.data?.items ?? []} getId={(r) => r.trip_id} sort={{ key: sort, order }} onSort={onSort}
            onRowClick={(r) => nav({ pathname: `/trips/${r.trip_id}`, search: "" })} empty={{ title: "No trips match", hint: "Try the All trips tab or clear the filters." }}
            columns={[
              { header: "Trip", cell: ({ row }) => <span className="mono font-bold">{row.original.trip_id.replace("trip-", "").slice(0, 14)}…</span> },
              { header: "Corridor", cell: ({ row }) => row.original.corridor },
              { header: "Type", cell: ({ row }) => row.original.route_type },
              { header: "Planned", meta: { align: "right", sortKey: "planned_min" }, cell: ({ row }) => mins(row.original.planned_min) },
              { header: "Actual", meta: { align: "right", sortKey: "actual_min" }, cell: ({ row }) => mins(row.original.actual_min) },
              { header: "Severe-delay risk", meta: { sortKey: "severe_probability" }, cell: ({ row }) => (
                <div className="flex min-w-[150px] items-center gap-2"><div className="w-20"><ProbabilityBar value={row.original.severe_probability} threshold={row.original.threshold} level={row.original.risk === "high" ? "high" : "low"} label="Severe-delay risk" /></div><span className="font-bold">{prob(row.original.severe_probability)}</span></div>) },
              { header: "Flag", cell: ({ row }) => <RiskBadge level={row.original.risk === "high" ? "high" : "low"} /> },
            ]}
          />
          {q.data && <Pager page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPage={(n) => set({ page: n }, false)} />}
        </>
      )}
      <span className="hidden">{sp.toString()}</span>
    </Card>
  );
}
