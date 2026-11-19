import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ClipboardCheck, Search } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, CardHeader, DataTable, EmptyState, ErrorState, LaneBars, RiskBadge, Skeleton } from "@/components/ui";
import { ApiError, get } from "@/lib/api";
import { inr, money, num, pct } from "@/lib/fmt";
import { useParams2 } from "@/lib/hooks";
import type { LaneGroup, LanePoints, LaneSearch, LaneVendor, Meta, TwoLegOption, VendorCompare } from "@/lib/types";
import { VendorDrawer } from "@/pages/Vendors";

const input = "h-9 w-full rounded-md border border-[#CBD0D6] bg-surface px-2.5 text-sm";
const FOOTER = "Cost comparison only; the data has no on-time or damage records, so 'cheapest' is not the same as 'best'.";

const Field = ({ id, label, children }: { id: string; label: string; children: ReactNode }) => (
  <div className="flex min-w-0 flex-col gap-1"><label htmlFor={id} className="text-xs font-semibold">{label}</label>{children}</div>
);
const Tile = ({ label, value, sub, testid }: { label: string; value: ReactNode; sub?: ReactNode; testid?: string }) => (
  <Card className="flex min-w-0 flex-col gap-0.5 px-4 py-3" data-testid={testid}>
    <div className="text-[12.5px] text-ink2">{label}</div>
    <div className="text-lg font-bold [font-variant-numeric:tabular-nums]">{value}</div>
    {sub && <div className="text-xs text-ink2">{sub}</div>}
  </Card>
);
const perMt = (v: number | null | undefined) => (v == null ? "–" : `${inr(v)}/MT`);
const shortDate = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
const lastUsed = (v: LaneVendor) => `${shortDate(v.last_used)} (${v.days_since_last_use} d)`;

function TagBadge({ v }: { v: LaneVendor }) {
  if (v.thin) return <span title={v.thin_reason ?? undefined}><RiskBadge level="grey" label="Thin data" hideIcon /></span>;
  if (v.tag === "Cheapest") return <RiskBadge level="low" label="Cheapest" />;
  if (v.tag === "Costly") return <RiskBadge level="high" label="Costly" />;
  return <RiskBadge level="neutral" label="Fair" hideIcon />;
}

type SortKey = "rank" | "vendor_code" | "rs_per_mt" | "median_rs_per_shipment" | "gap_to_fair_pct" | "lrs" | "utilisation_pct" | "margin_pct" | "last_used";
function sorted(rows: LaneVendor[], key: SortKey, order: "asc" | "desc"): LaneVendor[] {
  const val = (r: LaneVendor) => (key === "rank" ? (r.rank ?? 1e6 + r.rs_per_mt) : (r[key] as string | number | null));
  return [...rows].sort((a, b) => {
    const x = val(a), y = val(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const c = typeof x === "string" ? x.localeCompare(String(y)) : Number(x) - Number(y);
    return order === "asc" ? c : -c;
  });
}

function VendorTable({ g, onOpen }: { g: LaneGroup; onOpen: (code: string) => void }) {
  const [sort, setSort] = useState<{ key: SortKey; order: "asc" | "desc" }>({ key: "rank", order: "asc" });
  const rows = useMemo(() => sorted(g.vendors, sort.key, sort.order), [g, sort]);
  const onSort = (k: string) => setSort((s) => ({ key: k as SortKey, order: s.key === k && s.order === "asc" ? "desc" : "asc" }));
  return (
    <DataTable
      caption={`Vendors on this lane for ${g.label}`} data={rows} getId={(r) => r.vendor_code} sort={sort} onSort={onSort} onRowClick={(r) => onOpen(r.vendor_code)}
      columns={[
        { header: "Rank", meta: { sortKey: "rank" }, cell: ({ row }) => row.original.rank ?? "–" },
        { header: "Vendor", meta: { sortKey: "vendor_code" }, cell: ({ row }) => <span className="font-bold">{row.original.vendor_code}</span> },
        { header: "Tag", cell: ({ row }) => <TagBadge v={row.original} /> },
        { header: "Price per MT", meta: { align: "right", sortKey: "rs_per_mt" }, cell: ({ row }) => <b>{inr(row.original.rs_per_mt)}</b> },
        { header: "Per shipment", meta: { align: "right", sortKey: "median_rs_per_shipment" }, cell: ({ row }) => inr(row.original.median_rs_per_shipment) },
        { header: "Gap to fair", meta: { align: "right", sortKey: "gap_to_fair_pct" }, cell: ({ row }) => {
          const v = row.original;
          return v.gap_to_fair_pct == null ? <span title={`Shown only when it rests on 10+ fair-price-covered shipments (this vendor has ${v.n_gap_shipments})`}>{"–"}</span>
            : <span className="inline-flex items-center justify-end gap-1.5" title={`Median over ${v.n_gap_shipments} out-of-sample shipments`}>
              {v.gap_to_fair_pct > 0 ? "+" : ""}{v.gap_to_fair_pct.toFixed(0)}%{v.check_data && <span className="rounded-full bg-amber-tint px-1.5 text-[11px] font-bold text-amber-text" data-testid="check-data">check data</span>}</span>;
        } },
        { header: "LRs", meta: { align: "right", sortKey: "lrs" }, cell: ({ row }) => num(row.original.lrs) },
        { header: "Utilisation", meta: { align: "right", sortKey: "utilisation_pct" }, cell: ({ row }) => pct(row.original.utilisation_pct, 0) },
        { header: "Margin", meta: { align: "right", sortKey: "margin_pct" }, cell: ({ row }) => pct(row.original.margin_pct) },
        { header: "Last used", meta: { align: "right", sortKey: "last_used" }, cell: ({ row }) => lastUsed(row.original) },
      ]}
    />
  );
}

function TwoLeg({ options, label, open }: { options: TwoLegOption[]; label: string; open: boolean }) {
  return (
    <Card>
      <details open={open} data-testid="two-leg">
        <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm font-bold"><ChevronRight size={14} aria-hidden /> Two-leg options (indicative)<span className="font-normal text-ink2">{options.length} found</span></summary>
        <div className="border-t border-line px-4 py-3">
          <p className="m-0 mb-2 text-xs font-semibold text-amber-text">{label[0].toUpperCase() + label.slice(1)}. Never a substitute for the direct lane.</p>
          <DataTable caption="Two-leg options" data={options} getId={(r) => r.via + r.vehicle_type}
            columns={[
              { header: "Via", cell: ({ row }) => <b>{row.original.via}</b> },
              { header: "Vehicle", cell: ({ row }) => row.original.vehicle_type },
              { header: "Leg 1", cell: ({ row }) => `${row.original.leg1.vendor_code} at ${perMt(row.original.leg1.rs_per_mt)}` },
              { header: "Leg 2", cell: ({ row }) => `${row.original.leg2.vendor_code} at ${perMt(row.original.leg2.rs_per_mt)}` },
              { header: "Total", meta: { align: "right" }, cell: ({ row }) => <b>{perMt(row.original.total_rs_per_mt)}</b> },
              { header: "Direct", meta: { align: "right" }, cell: ({ row }) => perMt(row.original.direct_rs_per_mt) },
              { header: "Cheaper by", meta: { align: "right" }, cell: ({ row }) => (row.original.saving_pct == null ? "–" : pct(row.original.saving_pct, 0)) },
            ]} />
        </div>
      </details>
    </Card>
  );
}

function Results({ r, tab, setTab, openVendor }: { r: LaneSearch; tab: string; setTab: (t: string) => void; openVendor: (c: string) => void }) {
  const nav = useNavigate();
  const rec = r.recommendation;
  const g = r.groups.find((x) => x.label === tab) ?? r.groups.find((x) => rec && x.vehicle_type === rec.vehicle_type && x.basis === rec.basis) ?? r.groups[0];
  const s = r.summary;
  const shown = g ? g.vendors.filter((v) => !v.thin) : [];
  const chartRows = g ? (shown.length ? g.vendors : g.vendors).map((v) => ({ vendor: v.vendor_code, value: v.rs_per_mt, tag: v.tag, thin: v.thin })) : [];
  const saving = g ? g.vendors.reduce((a, v) => a + (v.saving_if_switched_rs ?? 0), 0) : 0;

  if (!r.has_direct || !g) {
    return (
      <div className="flex flex-col gap-4">
        <Card><EmptyState title={r.has_direct ? "No LRs in this period" : "No direct lane"} hint={r.notes[0] ?? "Try a longer period."} /></Card>
        {r.two_leg.options.length > 0 && <TwoLeg options={r.two_leg.options} label={r.two_leg.label} open />}
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {s && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5" data-testid="lane-summary">
          <Tile label="Distance" value={r.distance_km ? `${num(r.distance_km)} km` : "–"} sub={`Route ${r.route}`} />
          <Tile label="LRs on the lane" value={num(s.lrs)} sub={`${num(s.full_year_lrs)} in the full year`} />
          <Tile label="Vendors" value={num(s.vendors)} sub={`${s.focus.n_reliable} with enough data (${s.focus.vehicle_type})`} />
          <Tile label="Price per MT" value={s.focus.min_rs_per_mt == null ? "–" : `${inr(s.focus.min_rs_per_mt)} to ${inr(s.focus.max_rs_per_mt)}`}
            sub={s.focus.spread_pct == null ? "No comparison yet" : `${s.focus.spread_pct.toFixed(0)}% spread, ${s.focus.vehicle_type}`} />
          <Tile label="Lane margin" value={pct(s.margin_pct)} sub={`${r.period.from} to ${r.period.to}`} />
        </div>
      )}
      {r.notes.map((n) => <p key={n} role="status" className="m-0 rounded border border-line bg-amber-tint px-3 py-2 text-xs font-semibold text-amber-text">{n}</p>)}
      {rec && (
        <section className="rounded border border-line border-l-4 border-l-teal bg-surface px-4 py-3.5" data-testid="lane-recommendation">
          <div className="tag">Recommended: cheapest reliable vendor</div>
          <div className="my-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-xl font-bold" data-testid="rec-vendor">{rec.vendor_code}</span>
            <span className="text-sm font-semibold">{rec.vehicle_type}{rec.capacity_mt ? ` (${rec.capacity_mt} MT)` : ""}</span>
            <span className="text-sm text-ink2">Estimated {inr(rec.estimate?.estimate_rs ?? rec.rate_based_rs)}{rec.estimate?.estimate_rs == null ? " (rate x load)" : ""} for {rec.qty_mt ? `${rec.qty_mt.toFixed(rec.qty_assumed ? 1 : 0)} MT` : "this load"}{rec.qty_assumed ? " (typical load for this vehicle)" : ""}</span>
          </div>
          <p className="m-0">{perMt(rec.rs_per_mt)} vs lane median {perMt(rec.lane_median_rs_per_mt)}; {num(rec.lrs)} LRs; last used {rec.days_since_last_use === 0 ? "on the last day of data" : `${rec.days_since_last_use} days before the data ends`}.{rec.only_reliable_vendor ? " Only vendor with enough data." : ""}</p>
          <p className="mb-0 mt-1 text-xs text-ink2">{rec.estimate ? (rec.estimate.estimate_rs != null ? `${rec.estimate.basis}: V1 fair price ${inr(rec.estimate.fair_rs)}, typical gap ${(rec.estimate.vendor_gap_pct ?? 0) > 0 ? "+" : ""}${(rec.estimate.vendor_gap_pct ?? 0).toFixed(0)}%. Rate x load: ${inr(rec.rate_based_rs)}.` : `${rec.estimate.basis}. V1 fair price: ${inr(rec.estimate.fair_rs)}.`) : "Rate x load estimate; no V1 fair price is available for this lane."}</p>
          <div className="mt-3">
            <Button variant="primary" icon={<ClipboardCheck size={15} />} data-testid="check-booking" onClick={() => {
              const b = rec.booking_prefill;
              const q = new URLSearchParams({ route: b.route, vehicle_type: b.vehicle_type, vendor: b.vendor, ...(b.qty ? { qty: String(b.qty) } : {}), month: String(b.month), ...(b.quoted ? { quoted: String(b.quoted) } : {}) });
              nav(`/booking?${q.toString()}`);
            }}>Check this booking</Button>
          </div>
        </section>
      )}
      <Card>
        <CardHeader title="Vendors on this lane" sub={`${r.origin} to ${r.destination}, last ${r.months} months`} />
        <div role="tablist" aria-label="Vehicle type" className="flex flex-wrap gap-1 border-b border-line px-3 pt-2">
          {r.groups.map((x) => (
            <button key={x.label} role="tab" aria-selected={x.label === g.label} onClick={() => setTab(x.label)}
              className={`-mb-px rounded-t-md border border-b-0 px-3 py-1.5 text-[13px] font-semibold ${x.label === g.label ? "border-line bg-surface text-ink" : "border-transparent text-ink2 hover:text-ink"}`}>
              {x.label} <span className="font-normal text-ink2">{num(x.lrs)} LRs</span>
            </button>
          ))}
        </div>
        <VendorTable g={g} onOpen={openVendor} />
        <div className="flex flex-col gap-1 border-t border-line px-4 py-3 text-xs text-ink2">
          {g.thin_only && <span className="font-semibold text-amber-text">Thin data; showing all vendors without tags.</span>}
          {saving > 0 && <span>If every vendor on {g.label} charged the cheapest vendor's rate, the {r.months}-month bill would be <b className="text-ink">{money(saving)}</b> lower (indicative; ignores service quality).</span>}
          <span>{r.gap_note}</span>
        </div>
      </Card>
      <Card>
        <CardHeader title={`Price per MT by vendor, ${g.label}`} sub="dashed line = lane median" />
        <div className="px-4 py-3">
          <LaneBars data={chartRows} median={g.lane_median_rs_per_mt} format={perMt}
            label={`Price per MT by vendor for ${g.label}. ${shown[0] ? `Cheapest ${shown[0].vendor_code} at ${perMt(shown[0].rs_per_mt)}.` : ""} Lane median ${perMt(g.lane_median_rs_per_mt)}.`} />
        </div>
      </Card>
      {r.two_leg.options.length > 0 && <TwoLeg options={r.two_leg.options} label={r.two_leg.label} open={false} />}
    </div>
  );
}

function FindView() {
  const { get: p, set } = useParams2();
  const points = useQuery({ queryKey: ["lane-points"], queryFn: () => get<LanePoints>("/api/lanes/points"), staleTime: Infinity });
  const [f, setF] = useState({ from: p("from"), to: p("to"), qty: p("qty"), vt: p("vt"), months: p("months", "6") });
  const [err, setErr] = useState("");
  const upd = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const origin = points.data?.origins.find((o) => o.name.toLowerCase() === f.from.trim().toLowerCase());
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!f.from.trim() || !f.to.trim()) return setErr("Choose an origin and a destination.");
    if (f.qty && !(Number(f.qty) > 0 && Number(f.qty) <= 100)) return setErr("Load must be between 0 and 100 MT.");
    setErr("");
    set({ from: f.from.trim(), to: f.to.trim(), qty: f.qty, vt: f.vt, months: f.months, tab: null }, false);
  };
  const [from, to] = [p("from"), p("to")];
  const q = useQuery({
    queryKey: ["lane", from, to, p("qty"), p("vt"), p("months", "6")], enabled: !!(from && to),
    queryFn: () => get<LaneSearch>("/api/lanes/search", { from, to, qty: p("qty"), vehicle_type: p("vt"), months: p("months", "6") }),
  });
  return (
    <>
      <Card>
        <CardHeader title="Find the cheapest vendor from A to B" sub={points.data ? `${num(points.data.n_lanes)} lanes, ${num(points.data.n_origins)} origins` : undefined} />
        <form onSubmit={submit} noValidate className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_100px_120px_150px_auto] lg:items-end" aria-label="Lane search">
          <Field id="from" label="From (origin)">
            <input id="from" list="origins" className={input} value={f.from} onChange={upd("from")} autoComplete="off" placeholder="e.g. HAZIRA" />
            <datalist id="origins">{points.data?.origins.map((o) => <option key={o.name} value={o.name} />)}</datalist>
          </Field>
          <Field id="to" label="To (destination)">
            <input id="to" list="dests" className={input} value={f.to} onChange={upd("to")} autoComplete="off" placeholder={origin ? `${origin.destinations.length} reachable` : "e.g. KHADOLI"} />
            <datalist id="dests">{origin?.destinations.map((d) => <option key={d.name} value={d.name} />)}</datalist>
          </Field>
          <Field id="qty" label="Load (MT)"><input id="qty" inputMode="decimal" className={input} value={f.qty} onChange={upd("qty")} placeholder="optional" /></Field>
          <Field id="vt" label="Vehicle type">
            <select id="vt" className={input} value={f.vt} onChange={upd("vt")}><option value="">Any</option>{points.data?.vehicle_types.map((v) => <option key={v}>{v}</option>)}</select>
          </Field>
          <Field id="months" label="Period">
            <select id="months" className={input} value={f.months} onChange={upd("months")}><option value="3">Last 3 months</option><option value="6">Last 6 months</option><option value="12">Last 12 months</option></select>
          </Field>
          <Button type="submit" variant="primary" className="h-9" icon={<Search size={15} />}>Find vendors</Button>
          {err && <div role="alert" className="text-xs font-semibold text-red-dark sm:col-span-2 lg:col-span-6">{err}</div>}
        </form>
      </Card>
      <div className="min-w-0">
        {q.isFetching && !q.data && <div className="flex flex-col gap-4" aria-busy="true"><Skeleton className="h-20" /><Skeleton className="h-40" /><Skeleton className="h-56" /></div>}
        {q.isError && <Card><ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} onRetry={q.error instanceof ApiError && q.error.status === 404 ? undefined : () => q.refetch()} /></Card>}
        {q.data && <Results r={q.data} tab={p("tab")} setTab={(t) => set({ tab: t }, false)} openVendor={(c) => set({ v: c }, false)} />}
        {!from && !to && !q.data && <Card><EmptyState title="Pick an origin and a destination" hint="You get every vendor on the lane ranked from cheapest to costliest, compared like for like by vehicle type." /></Card>}
      </div>
      {p("v") && <VendorDrawer code={p("v").toUpperCase()} onClose={() => set({ v: null }, false)} />}
    </>
  );
}

function CompareView() {
  const { get: p, set } = useParams2();
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/api/meta/options"), staleTime: Infinity });
  const initial = p("codes").split(",").filter(Boolean);
  const [codes, setCodes] = useState<string[]>([initial[0] ?? "", initial[1] ?? "", initial[2] ?? "", initial[3] ?? ""]);
  const [err, setErr] = useState("");
  const chosen = p("codes").split(",").filter(Boolean);
  const q = useQuery({ queryKey: ["compare", p("codes")], enabled: chosen.length >= 2, queryFn: () => get<VendorCompare>("/api/vendors/compare", { codes: p("codes"), months: 12 }) });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const list = codes.map((c) => c.trim().toUpperCase()).filter(Boolean);
    if (list.length < 2) return setErr("Enter at least two vendor codes, for example V013 and V023.");
    if (list.some((c) => !/^V\d{3,}$/.test(c))) return setErr("Vendor codes look like V013.");
    setErr("");
    set({ codes: list.join(",") }, false);
  };
  const r = q.data;
  return (
    <>
      <Card>
        <CardHeader title="Compare 2 to 4 vendors head to head" sub="On lanes where each has 10+ LRs, last 12 months" />
        <form onSubmit={submit} noValidate className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_1fr_auto] lg:items-end" aria-label="Compare vendors">
          {codes.map((c, i) => (
            <Field key={i} id={`cmp${i}`} label={`Vendor ${i + 1}${i > 1 ? " (optional)" : ""}`}>
              <input id={`cmp${i}`} list="cmp-vendors" className={input} value={c} autoComplete="off" placeholder="e.g. V013" onChange={(e) => setCodes((x) => x.map((y, j) => (j === i ? e.target.value : y)))} />
            </Field>
          ))}
          <datalist id="cmp-vendors">{meta.data?.vendors.map((v) => <option key={v.vendor_code} value={v.vendor_code} />)}</datalist>
          <Button type="submit" variant="primary" className="h-9" icon={<Search size={15} />}>Compare vendors</Button>
          {err && <div role="alert" className="text-xs font-semibold text-red-dark sm:col-span-2 lg:col-span-5">{err}</div>}
        </form>
      </Card>
      {q.isFetching && !r && <Skeleton className="h-48" />}
      {q.isError && <Card><ErrorState message={q.error instanceof ApiError ? q.error.message : "Unexpected error"} /></Card>}
      {r && (
        <div className="flex min-w-0 flex-col gap-4" data-testid="compare-result">
          {r.notes.map((n) => <p key={n} role="status" className="m-0 rounded border border-line bg-amber-tint px-3 py-2 text-xs font-semibold text-amber-text">{n}</p>)}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {r.vendors.map((v) => (
              <Tile key={v.vendor_code} label={`${v.vendor_code}: price index vs lane median`} testid={`compare-${v.vendor_code}`} value={v.index_vs_lane_median == null ? "–" : v.index_vs_lane_median.toFixed(0)}
                sub={`Won ${v.lanes_won} of ${r.n_shared_lanes} shared lanes; ${money(v.extra_cost_vs_cheapest_rs)} above the cheapest chosen rate`} />
            ))}
          </div>
          <p className="m-0 text-xs text-ink2">{r.index_note} {r.extra_cost_note}</p>
          <Card>
            <CardHeader title="Shared lanes" sub={`${r.n_shared_lanes} lane and vehicle combinations`} />
            <DataTable caption="Shared lanes" data={r.lanes.slice(0, 60)} getId={(x) => `${x.origin}${x.destination}${x.vehicle_type}${x.basis}`}
              empty={{ title: "No shared lanes", hint: "These vendors do not both have 10+ LRs on any lane in this period." }}
              columns={[
                { header: "Lane", cell: ({ row }) => <span className="font-bold">{row.original.origin} to {row.original.destination}</span> },
                { header: "Vehicle", cell: ({ row }) => row.original.vehicle_type },
                ...r.codes.map((c) => ({ header: `${c} per MT`, meta: { align: "right" as const }, cell: ({ row }: { row: { original: VendorCompare["lanes"][number] } }) => <span className={row.original.cheaper === c ? "font-bold text-teal" : ""}>{inr(row.original.rs_per_mt[c])}</span> })),
                { header: "Cheaper", cell: ({ row }) => <b>{row.original.cheaper}</b> },
                { header: "Gap", meta: { align: "right" }, cell: ({ row }) => pct(row.original.gap_pct, 0) },
              ]} />
          </Card>
        </div>
      )}
      {!r && !q.isFetching && !q.isError && <Card><EmptyState title="Choose two vendors" hint="You get the lanes they share, the price each charges, and which one is cheaper." /></Card>}
    </>
  );
}

export default function Lanes() {
  const { get: p, set } = useParams2();
  const view = p("view", "find");
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div role="tablist" aria-label="Lane finder views" className="flex gap-1">
        {[["find", "Find a lane"], ["compare", "Compare vendors"]].map(([k, label]) => (
          <button key={k} role="tab" aria-selected={view === k} onClick={() => set({ view: k === "find" ? null : k }, false)}
            className={`rounded-md border px-3.5 py-1.5 text-[13px] font-semibold ${view === k ? "border-navy bg-navy text-white" : "border-line bg-surface text-ink2 hover:text-ink"}`}>{label}</button>
        ))}
      </div>
      {view === "compare" ? <CompareView /> : <FindView />}
      <p className="m-0 text-xs text-ink2" data-testid="lane-footer">{FOOTER}</p>
    </div>
  );
}
