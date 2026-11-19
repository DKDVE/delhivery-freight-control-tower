import { useMutation, useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { DraftPanel } from "@/components/DraftPanel";
import { Button, Card, CardHeader, DataTable, Drawer, ErrorState, Pager, RiskBadge, Skeleton, TrendChart } from "@/components/ui";
import { ApiError, get, post } from "@/lib/api";
import { money, monthLabel, num, pct } from "@/lib/fmt";
import { useParams2 } from "@/lib/hooks";
import type { Draft, Page, VendorDetail, VendorRow } from "@/lib/types";

const input = "h-9 rounded-md border border-[#CBD0D6] bg-surface px-2.5 text-sm";

export function VendorDrawer({ code, onClose }: { code: string; onClose?: () => void }) {
  const nav = useNavigate();
  const { sp } = useParams2();
  const q = useQuery({ queryKey: ["vendor", code], queryFn: () => get<VendorDetail>(`/api/vendors/${code}`) });
  const draft = useMutation({ mutationFn: () => post<Draft>("/api/drafts", { kind: "renegotiation", vendor: code }) });
  const close = onClose ?? (() => nav({ pathname: "/vendors", search: sp.toString() }));
  const v = q.data;
  const flagged = v ? v.loss_share_pct > 25 || (v.overpay_pct ?? 0) > 10 : false;
  return (
    <Drawer open onClose={close} title={`Vendor ${code}`} kicker="Vendor profile" badge={v && <RiskBadge level={flagged ? "high" : "low"} label={flagged ? "Flagged" : "Within range"} />}
      footer={v && <Button variant="primary" icon={<FileText size={15} />} disabled={draft.isPending} onClick={() => draft.mutate()}>{draft.isPending ? "Drafting…" : "Draft renegotiation"}</Button>}>
      {q.isPending && <Skeleton className="h-64" />}
      {q.isError && <ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} onRetry={() => q.refetch()} />}
      {v && (
        <>
          <div className="grid grid-cols-2 gap-3">
            {[["Spend", money(v.spend_rs)], ["LRs", num(v.lrs)], ["Margin", pct(v.margin_pct)], ["Loss-making LRs", pct(v.loss_share_pct, 0)], ["Money lost", money(v.loss_rs)], ["Vs fair price", v.overpay_pct == null ? "–" : `${v.overpay_pct > 0 ? "+" : ""}${v.overpay_pct.toFixed(0)}%`]].map(([k, val]) => (
              <div key={k} className="rounded border border-line px-3 py-2"><div className="text-xs text-ink2">{k}</div><div className="text-lg font-bold">{val}</div></div>
            ))}
          </div>
          <p className="m-0 text-xs text-ink2">Period {v.period}</p>
          <div>
            <h3 className="mb-1 text-sm font-bold">Margin by month</h3>
            <TrendChart height={170} data={v.monthly.map((m) => ({ month: monthLabel(m.month), margin: m.margin_pct }))} xKey="month" yKey="margin" label={`Monthly margin for ${code}`} />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-bold">Worst lanes</h3>
            <DataTable caption={`Worst lanes for ${code}`} data={v.worst_lanes} getId={(r) => r.route + r.vehicle_type}
              columns={[
                { header: "Route", cell: ({ row }) => <span className="font-bold">{row.original.route}</span> },
                { header: "Vehicle", cell: ({ row }) => row.original.vehicle_type },
                { header: "LRs", meta: { align: "right" }, cell: ({ row }) => num(row.original.lrs) },
                { header: "Lost", meta: { align: "right" }, cell: ({ row }) => <span className="font-bold text-red-dark">{money(row.original.loss_rs)}</span> },
              ]} />
          </div>
          {draft.isError && <ErrorState message={draft.error instanceof ApiError ? draft.error.message : "Could not draft"} onRetry={() => draft.mutate()} />}
          {draft.data && <DraftPanel draft={draft.data} />}
        </>
      )}
    </Drawer>
  );
}

export default function Vendors() {
  const { code } = useParams();
  const nav = useNavigate();
  const { get: p, set, sp } = useParams2();
  const sort = p("sort", "spend_rs");
  const order = p("order", "desc") as "asc" | "desc";
  const page = Number(p("page", "1"));
  const flagged = p("flagged") === "1";
  const params = { q: p("q"), flagged, min_lrs: p("min_lrs", "10"), sort, order, page, page_size: 25 };
  const q = useQuery({ queryKey: ["vendors", params], queryFn: () => get<Page<VendorRow>>("/api/vendors", params), placeholderData: (d) => d });
  const onSort = (k: string) => set({ sort: k, order: sort === k && order === "desc" ? "asc" : "desc" });

  return (
    <>
      <Card>
        <CardHeader title="Vendors" sub={q.data ? `${num(q.data.total)} vendors with ${params.min_lrs}+ LRs` : undefined} />
        <div className="flex flex-wrap items-end gap-3 border-b border-line px-4 py-3">
          <div className="flex flex-col gap-1"><label htmlFor="vq" className="text-xs font-semibold">Vendor code</label><input id="vq" className={input} placeholder="e.g. V012" value={p("q")} onChange={(e) => set({ q: e.target.value })} /></div>
          <div className="flex flex-col gap-1"><label htmlFor="vm" className="text-xs font-semibold">Minimum LRs</label><input id="vm" inputMode="numeric" className={`${input} w-24`} value={p("min_lrs", "10")} onChange={(e) => set({ min_lrs: e.target.value.replace(/\D/g, "") })} /></div>
          <label className="flex h-9 items-center gap-2 text-[13px]"><input type="checkbox" checked={flagged} onChange={(e) => set({ flagged: e.target.checked ? "1" : null })} /> Flagged only</label>
        </div>
        {q.isError ? <ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} onRetry={() => q.refetch()} /> : (
          <>
            <DataTable
              caption="Vendors" loading={q.isPending} data={q.data?.items ?? []} getId={(r) => r.vendor_code} sort={{ key: sort, order }} onSort={onSort} selectedId={code}
              onRowClick={(r) => nav({ pathname: `/vendors/${r.vendor_code}`, search: sp.toString() })} empty={{ title: "No vendors match", hint: "Lower the minimum LRs or clear the filters." }}
              columns={[
                { header: "Vendor", meta: { sortKey: "vendor_code" }, cell: ({ row }) => <span className="font-bold">{row.original.vendor_code}</span> },
                { header: "LRs", meta: { align: "right", sortKey: "lrs" }, cell: ({ row }) => num(row.original.lrs) },
                { header: "Spend", meta: { align: "right", sortKey: "spend_rs" }, cell: ({ row }) => money(row.original.spend_rs) },
                { header: "Margin", meta: { align: "right", sortKey: "margin_pct" }, cell: ({ row }) => pct(row.original.margin_pct) },
                { header: "Loss-making", meta: { align: "right", sortKey: "loss_share_pct" }, cell: ({ row }) => pct(row.original.loss_share_pct, 0) },
                { header: "Money lost", meta: { align: "right", sortKey: "loss_rs" }, cell: ({ row }) => money(row.original.loss_rs) },
                { header: "Vs fair price", meta: { align: "right", sortKey: "overpay_pct" }, cell: ({ row }) => { const o = row.original.overpay_pct; return o == null ? "–" : <span className={o > 10 ? "font-bold text-red-dark" : ""}>{o > 0 ? "+" : ""}{o.toFixed(0)}%</span>; } },
              ]}
            />
            {q.data && <Pager page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPage={(n) => set({ page: n }, false)} />}
          </>
        )}
      </Card>
      {code && <VendorDrawer code={code.toUpperCase()} />}
    </>
  );
}
