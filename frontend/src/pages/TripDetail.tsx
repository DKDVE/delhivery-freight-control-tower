import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bell, Mail } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { DraftPanel } from "@/components/DraftPanel";
import { Button, Card, CardHeader, DataTable, ErrorState, ProbabilityBar, RiskBadge } from "@/components/ui";
import { ApiError, get, post } from "@/lib/api";
import { humanise, mins, num, prob } from "@/lib/fmt";
import { QueryBoundary } from "@/lib/hooks";
import type { Draft, TripDetail as TD } from "@/lib/types";

export default function TripDetail() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["trip", id], queryFn: () => get<TD>(`/api/trips/${encodeURIComponent(id)}`) });
  const draft = useMutation({ mutationFn: (kind: "hub_alert" | "customer_eta") => post<Draft>("/api/drafts", { kind, trip_id: id }) });
  return (
    <>
      <Link to="/trips" className="inline-flex items-center gap-1 text-[13px] font-semibold text-navy"><ArrowLeft size={14} aria-hidden /> All trips</Link>
      <QueryBoundary query={q} skeleton={<div aria-busy="true" className="h-64 animate-pulse rounded bg-grid" />}>
        {(t) => {
          const maxLeg = Math.max(...t.legs.map((l) => Math.max(l.planned_min, l.actual_min)), 1);
          return (
            <>
              <Card>
                <CardHeader title={`Trip ${t.trip_id.replace("trip-", "").slice(0, 18)}…`} sub={`${t.corridor} · ${t.route_type}`} right={<RiskBadge level={t.severe_delay.flag ? "high" : "low"} label={t.severe_delay.flag ? "Likely severe delay" : "Delay not flagged"} />} />
                <div className="grid gap-4 p-4 md:grid-cols-3">
                  <div><div className="text-xs text-ink2">Planned</div><div className="text-xl font-bold">{mins(t.planned_min)}</div></div>
                  <div><div className="text-xs text-ink2">Predicted (80% range)</div><div className="text-xl font-bold">{mins(t.predicted_min)}</div><div className="text-xs text-ink2">{mins(t.predicted_low_min)} to {mins(t.predicted_high_min)} {"·"} {t.predicted_vs_plan_ratio.toFixed(1)}x plan</div></div>
                  <div><div className="text-xs text-ink2">Severe-delay risk</div><div className="text-xl font-bold">{prob(t.severe_delay.probability, 1)}</div><ProbabilityBar value={t.severe_delay.probability} threshold={t.severe_delay.threshold} level={t.severe_delay.flag ? "high" : "low"} label="Severe-delay risk" /></div>
                </div>
              </Card>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Leg timeline" sub="planned vs actual" />
                  <ol className="m-0 list-none space-y-3 p-4" aria-label="Legs">
                    {t.legs.map((l) => (
                      <li key={l.leg}>
                        <div className="flex justify-between text-[12.5px]"><span className="font-bold">Leg {l.leg}: {l.from} {"→"} {l.to}</span><span className="text-ink2">planned {mins(l.planned_min)} {"·"} actual {mins(l.actual_min)}</span></div>
                        <div className="mt-1 space-y-1" role="img" aria-label={`Leg ${l.leg} planned ${mins(l.planned_min)}, actual ${mins(l.actual_min)}`}>
                          <div className="h-2 rounded-sm bg-navy" style={{ width: `${(l.planned_min / maxLeg) * 100}%` }} />
                          <div className={`h-2 rounded-sm ${l.actual_min > l.planned_min * 1.5 ? "bg-red" : "bg-teal"}`} style={{ width: `${(l.actual_min / maxLeg) * 100}%` }} />
                        </div>
                        {l.handling_min != null && <div className="mt-0.5 text-xs text-ink2">Handling at the hub: {mins(l.handling_min)}</div>}
                      </li>
                    ))}
                  </ol>
                  <p className="m-0 border-t border-line px-4 py-2 text-xs text-ink2"><b className="text-navy">Navy</b> planned, <b className="text-teal">teal</b> actual, <b className="text-red-dark">red</b> actual above 1.5x plan.</p>
                </Card>
                <Card>
                  <CardHeader title="Why: top drivers of delay risk" sub="SHAP, this trip" />
                  <DataTable caption="Top drivers" data={t.severe_delay.top_drivers} getId={(r) => r.feature}
                    columns={[
                      { header: "Driver", cell: ({ row }) => <span className="font-bold">{humanise(row.original.feature)}</span> },
                      { header: "Value", meta: { align: "right" }, cell: ({ row }) => typeof row.original.value === "number" ? num(row.original.value, 1) : String(row.original.value ?? "unknown") },
                      { header: "Effect", cell: ({ row }) => <span className={`font-bold ${row.original.shap > 0 ? "text-red-dark" : "text-teal"}`}>{row.original.shap > 0 ? "raises" : "lowers"} risk</span> },
                    ]} />
                  {t.origin_hub && <p className="m-0 border-t border-line px-4 py-2.5 text-xs text-ink2">Origin hub {t.origin_hub.source_center} holds freight {mins(t.origin_hub.mean_actual_handling_min)} on average against a network median of {mins(t.network_median_handling_min)}.</p>}
                </Card>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" icon={<Bell size={15} />} disabled={draft.isPending} onClick={() => draft.mutate("hub_alert")}>Draft hub alert</Button>
                <Button icon={<Mail size={15} />} disabled={draft.isPending} onClick={() => draft.mutate("customer_eta")}>Draft customer ETA</Button>
              </div>
              {draft.isError && <Card><ErrorState message={draft.error instanceof ApiError ? draft.error.message : "Could not draft"} onRetry={() => draft.mutate(draft.variables!)} /></Card>}
              {draft.data && <DraftPanel draft={draft.data} />}
            </>
          );
        }}
      </QueryBoundary>
    </>
  );
}
