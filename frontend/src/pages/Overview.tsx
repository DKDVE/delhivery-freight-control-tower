import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Card, CardHeader, DataTable, KpiTile, RiskBadge, Skeleton, TrendChart } from "@/components/ui";
import { get } from "@/lib/api";
import { money, monthLabel, num, pct, prob } from "@/lib/fmt";
import { QueryBoundary } from "@/lib/hooks";
import type { Overview as OverviewData } from "@/lib/types";

const Loading = () => (
  <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading overview">
    <div className="grid grid-cols-2 gap-4 md:grid-cols-5">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[104px]" />)}</div>
    <Skeleton className="h-72" />
  </div>
);

export default function Overview() {
  const q = useQuery({ queryKey: ["overview"], queryFn: () => get<OverviewData>("/api/overview") });
  return (
    <QueryBoundary query={q} skeleton={<Loading />}>
      {(o) => {
        const worst = o.monthly_margin.reduce((a, b) => (b.margin_pct < a.margin_pct ? b : a));
        const best = o.monthly_margin.reduce((a, b) => (b.margin_pct > a.margin_pct ? b : a));
        const chart = o.monthly_margin.map((m) => ({ month: monthLabel(m.month), margin: m.margin_pct }));
        return (
          <>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
              {o.kpis.map((k, i) => (
                <div key={k.id} className={"[&>*]:h-full " + (i === o.kpis.length - 1 ? "col-span-2 md:col-span-1" : "")}><KpiTile kpi={k} vs="vs last qtr" /></div>
              ))}
            </div>
            <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
              <Card>
                <CardHeader title={`Margin swung between ${pct(worst.margin_pct)} and ${pct(best.margin_pct)}, with ${monthLabel(worst.month)} the weakest month`} />
                <div className="p-4">
                  <TrendChart data={chart} xKey="month" yKey="margin" label="Monthly margin on billed freight" />
                  <p className="mb-0 mt-1.5 text-xs text-ink2">Margin on billed freight by month, valid-cost LRs only</p>
                </div>
              </Card>
              <Card>
                <CardHeader title="Alerts needing a decision" sub={`${o.alerts.length} open`} />
                <ul className="m-0 list-none p-0" aria-label="Alerts">
                  {o.alerts.map((a) => (
                    <li key={a.kind + a.id} className="border-b border-grid last:border-0">
                      <Link to={a.href} className="flex items-center gap-3 px-4 py-2.5 hover:bg-bg">
                        {a.kind === "trip" ? <RiskBadge level="high" /> : <RiskBadge level="medium" label="Review" />}
                        <div className="min-w-0 flex-1">
                          {a.kind === "trip" ? (
                            <>
                              <div className="truncate font-bold">Trip {a.id.slice(5, 17)}… likely to run 2.5x its plan</div>
                              <div className="text-xs text-ink2">Severe-delay risk {prob(a.probability, 1)} · needs a dispatcher decision</div>
                            </>
                          ) : (
                            <>
                              <div className="font-bold">Vendor {a.id} paid above the fair price</div>
                              <div className="text-xs text-ink2">{money(a.excess_rs)} above fair in the test quarter · needs a renegotiation decision</div>
                            </>
                          )}
                        </div>
                        <ChevronRight size={16} aria-hidden className="flex-none text-ink2" />
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
            <Card>
              <CardHeader title="Top 5 lanes by money lost" sub="route × vehicle type, 10+ LRs" right={<Link className="inline-flex items-center gap-1 text-xs font-semibold text-navy" to="/vendors">View all vendors <ChevronRight size={14} aria-hidden /></Link>} />
              <DataTable
                caption="Top five lanes by money lost" data={o.top_lanes_by_loss} getId={(r) => r.route + r.vehicle_type}
                columns={[
                  { header: "Route", cell: ({ row }) => <span className="font-bold">{row.original.route}</span> },
                  { header: "Vehicle", cell: ({ row }) => row.original.vehicle_type },
                  { header: "LRs", meta: { align: "right" }, cell: ({ row }) => num(row.original.lrs) },
                  { header: "Loss-making", meta: { align: "right" }, cell: ({ row }) => pct(row.original.loss_share_pct, 0) },
                  { header: "Money lost", meta: { align: "right" }, cell: ({ row }) => <span className="font-bold text-red-dark">{money(row.original.loss_rs)}</span> },
                ]}
              />
            </Card>
            <p className="m-0 text-xs text-ink2">
              Data as of {o.as_of.vendor_data.to} (vendor file, {num(o.as_of.vendor_data.lrs)} valid-cost LRs) and {o.as_of.middle_mile_data.to} (middle-mile trips).
            </p>
          </>
        );
      }}
    </QueryBoundary>
  );
}
