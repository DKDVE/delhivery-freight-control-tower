import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, DataTable, HBarChart, Skeleton } from "@/components/ui";
import { get } from "@/lib/api";
import { mins, money, num } from "@/lib/fmt";
import { QueryBoundary } from "@/lib/hooks";
import type { Hubs as H } from "@/lib/types";

export default function Hubs() {
  const q = useQuery({ queryKey: ["hubs"], queryFn: () => get<H>("/api/hubs", { top: 25 }) });
  return (
    <QueryBoundary query={q} skeleton={<Skeleton className="h-96" />}>
      {(h) => {
        const top = h.hubs.slice(0, 10);
        return (
          <>
            <Card>
              <CardHeader title={`${top[0]?.source_center ?? "Top hub"} loses the most time holding freight`} sub={`excess hours vs prediction, top 10 of ${h.hubs.length}`} />
              <div className="p-4"><HBarChart data={top.map((x) => ({ hub: x.source_center, hours: x.excess_hours }))} labelKey="hub" valueKey="hours" unit=" h" label="Excess handling hours by hub" /></div>
            </Card>
            <Card>
              <CardHeader title="Hub ranking" sub={`network median handling ${mins(h.network_median_handling_min)}`} />
              <DataTable caption="Hubs ranked by excess handling hours" data={h.hubs} getId={(r) => r.source_center}
                columns={[
                  { header: "Hub", cell: ({ row }) => <span className="font-bold">{row.original.source_center}</span> },
                  { header: "Legs", meta: { align: "right" }, cell: ({ row }) => num(row.original.legs) },
                  { header: "Actual handling", meta: { align: "right" }, cell: ({ row }) => mins(row.original.mean_actual_handling_min) },
                  { header: "Predicted", meta: { align: "right" }, cell: ({ row }) => mins(row.original.mean_predicted_handling_min) },
                  { header: "Excess hours", meta: { align: "right" }, cell: ({ row }) => <span className="font-bold text-red-dark">{num(row.original.excess_hours)}</span> },
                  { header: "Cost at base rate", meta: { align: "right" }, cell: ({ row }) => money(row.original.excess_rs_base) },
                ]} />
              <p className="m-0 border-t border-line px-4 py-2.5 text-xs text-ink2">{h.rate_label}</p>
            </Card>
          </>
        );
      }}
    </QueryBoundary>
  );
}
