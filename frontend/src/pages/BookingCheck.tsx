import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, ClipboardCheck, Pencil, ShieldCheck, Wrench, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Button, Card, CardHeader, DataTable, ErrorState, ProbabilityBar, RiskBadge, Skeleton, Stepper } from "@/components/ui";
import { ApiError, get, post } from "@/lib/api";
import { cap, humanise, inr, pct, prob } from "@/lib/fmt";
import { useDecision, useParams2 } from "@/lib/hooks";
import type { Booking, BookingRequest, Meta } from "@/lib/types";

const Field = ({ id, label, hint, error, children }: { id: string; label: string; hint?: ReactNode; error?: string; children: ReactNode }) => (
  <div className="flex flex-col gap-1.5">
    <label htmlFor={id} className="text-[12.5px] font-semibold">{label}</label>
    {children}
    {error ? <div id={`${id}-err`} role="alert" className="text-[11.5px] font-semibold text-red-dark">{error}</div> : hint && <div className="text-[11.5px] text-ink2">{hint}</div>}
  </div>
);
const input = "h-9 w-full rounded-md border border-[#CBD0D6] bg-surface px-2.5 text-sm";

function PriceRange({ fair, low, high, quote }: { fair: number; low: number; high: number; quote?: number }) {
  const base = Math.min(low, quote ?? low) * 0.7;
  const span = high * 1.03 - base;
  const pos = (v: number) => `${((v - base) / span) * 100}%`;
  return (
    <div>
      <div className="relative my-3 h-[30px]" role="img" aria-label={`Fair price ${inr(fair)}, 80 percent range ${inr(low)} to ${inr(high)}${quote ? `, quote ${inr(quote)}` : ""}`}>
        <div className="absolute inset-x-0 top-3 h-1.5 rounded bg-grid" />
        <div className="absolute top-2.5 h-2.5 rounded border border-[#b8c4d6] bg-navy-tint" style={{ left: pos(low), width: `${((high - low) / span) * 100}%` }} />
        <div className="absolute top-[3px] h-6 w-[3px] bg-navy" style={{ left: pos(fair) }} />
        {quote != null && <div className="absolute top-[3px] h-6 w-[3px] bg-red" style={{ left: pos(quote) }} />}
      </div>
      <div className="flex flex-col gap-0.5 text-xs text-ink2">
        <span><b className="text-navy">|</b> Fair price {inr(fair)}</span>
        {quote != null && <span><b className="text-red">|</b> Quote {inr(quote)}</span>}
        <span>80% range {inr(low)} to {inr(high)}</span>
      </div>
    </div>
  );
}

function Result({ b }: { b: Booking }) {
  const { state, decide } = useDecision(b.run_id);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(b.draft ?? "");
  useEffect(() => { setText(b.draft ?? ""); setEditing(false); }, [b.run_id, b.draft]);
  const { loss_risk: lr, fair_price: fp, underload: ul, policy } = b;
  const gap = fp.overpay_pct;
  const maxShap = Math.max(...lr.top_drivers.map((d) => Math.abs(d.shap)));
  const reason = b.summary.includes("because") ? cap(b.summary.split("because")[1].trim()) : b.summary;
  const step = state ? 2 : 1;
  const tone = b.recommended_action === "proceed" ? "border-l-teal" : "border-l-amber";

  return (
    <div className="flex min-w-0 flex-col gap-4" aria-live="polite" data-testid="booking-result">
      <Stepper steps={["Inputs", "Review the recommendation", "Decide"]} current={step} />
      <section className={`rounded border border-line border-l-4 bg-surface px-4 py-3.5 ${tone}`} data-testid="recommendation">
        <div className="tag">Recommended action {"·"} chosen by rules</div>
        <div className="my-1 flex flex-wrap items-center gap-2.5">
          <span className="text-xl font-bold" data-testid="action">{cap(b.recommended_action)}</span>
          <span className="inline-flex h-[22px] items-center gap-1 rounded-full bg-amber-tint px-2 text-xs font-bold text-amber-text"><Wrench size={13} aria-hidden /> signal: {humanise(policy.signal)}</span>
        </div>
        <p className="m-0">{reason}</p>
        <div className="my-3 h-px bg-line" />
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink2">
          <ShieldCheck size={13} aria-hidden /> Guardrail: {b.guardrail_status === "passed" ? "Passed" : "Replaced by rule-based answer"} {"·"} mode {b.mode}
          {b.banner && <span className="font-semibold text-amber-text">{b.banner}</span>}
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="flex flex-col gap-1.5 px-4 py-3.5">
          <div className="text-[12.5px] text-ink2">Loss risk (V2)</div>
          <div className="flex items-center gap-2.5"><span className="text-2xl font-bold">{prob(lr.probability)}</span><RiskBadge level={lr.level} /></div>
          <ProbabilityBar value={lr.probability} threshold={lr.threshold} level={lr.level} label="Loss risk" />
          <div className="text-[11.5px] text-ink2">Flag threshold {prob(lr.threshold, 1)} (marker)</div>
        </Card>
        <Card className="flex flex-col gap-1.5 px-4 py-3.5">
          <div className="text-[12.5px] text-ink2">Fair price vs quote (V1)</div>
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-2xl font-bold">{inr(fp.fair_rs)}</span>
            {gap != null && <RiskBadge level={Math.abs(gap) > 30 ? "medium" : "low"} label={`${Math.abs(gap).toFixed(0)}% ${gap < 0 ? "below" : "above"} fair`} />}
          </div>
          <PriceRange fair={fp.fair_rs} low={fp.fair_low_rs} high={fp.fair_high_rs} quote={fp.quoted_rs} />
        </Card>
        <Card className="flex flex-col gap-1.5 px-4 py-3.5">
          <div className="text-[12.5px] text-ink2">Under-load risk (V3)</div>
          <div className="flex items-center gap-2.5"><span className="text-2xl font-bold">{prob(ul.probability)}</span><RiskBadge level={ul.flag ? "high" : "low"} /></div>
          <ProbabilityBar value={ul.probability} threshold={ul.threshold} level={ul.flag ? "high" : "low"} label="Under-load risk" />
          <div className="text-[11.5px] text-ink2">Truck would be {pct(ul.expected_utilisation_pct, 0)} full {"·"} threshold {prob(ul.threshold, 1)}</div>
        </Card>
      </div>

      {b.warnings.length > 0 && <div role="note" className="rounded border border-amber bg-amber-tint px-3 py-2 text-xs text-amber-text">{b.warnings.join(" ")}</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Why: top 3 drivers of loss risk" sub="SHAP, this booking" />
          <div className="p-4">
            {lr.top_drivers.map((d) => (
              <div key={d.feature} className="my-2 grid grid-cols-[120px_1fr] items-center gap-3 sm:grid-cols-[170px_1fr]">
                <div><div className="font-bold">{humanise(d.feature)}</div><div className="text-xs text-ink2">value {typeof d.value === "number" ? d.value.toFixed(1) : String(d.value ?? "unknown")}</div></div>
                <div className="flex items-center gap-2">
                  <div className={`h-2.5 max-w-full rounded-sm ${d.shap > 0 ? "bg-red" : "bg-teal"}`} style={{ width: `${(Math.abs(d.shap) / maxShap) * 70}%`, minWidth: 8 }} />
                  <span className={`text-xs font-bold ${d.shap > 0 ? "text-red-dark" : "text-teal"}`}>{d.shap > 0 ? "raises" : "lowers"} loss risk</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <CardHeader title="Cheaper vendors on this lane" sub={b.current_rs_per_mt ? `now ${inr(b.current_rs_per_mt)} per MT` : undefined} />
          <DataTable
            caption="Alternative vendors on this lane" data={b.alternatives} getId={(r) => r.vendor_code} empty={{ title: "No alternatives", hint: "No other vendor has 10 or more LRs on this lane." }}
            columns={[
              { header: "Vendor", cell: ({ row }) => <span className="font-bold">{row.original.vendor_code}</span> },
              { header: "Per MT", meta: { align: "right" }, cell: ({ row }) => inr(row.original.rs_per_mt) },
              { header: "Margin", meta: { align: "right" }, cell: ({ row }) => pct(row.original.margin_pct, 0) },
              { header: "LRs", meta: { align: "right" }, cell: ({ row }) => row.original.lr_count },
              { header: "vs now", meta: { align: "right" }, cell: ({ row }) => { const d = b.current_rs_per_mt ? (row.original.rs_per_mt / b.current_rs_per_mt - 1) * 100 : null; return d == null ? "–" : <span className={`font-bold ${d < 0 ? "text-teal" : "text-red-dark"}`}>{d > 0 ? "+" : ""}{d.toFixed(0)}%</span>; } },
            ]}
          />
        </Card>
      </div>

      <Card>
        <CardHeader title="Draft message" sub={b.draft_template ? `template: ${humanise(b.draft_template)}` : undefined}
          right={<span className="rounded-full bg-[#EEF0F3] px-2 py-0.5 text-xs font-bold text-ink2">{state ? (state.saved ? "Decision saved" : "Saving…") : "Not sent"}</span>} />
        <div className="p-4">
          {b.draft ? (
            <>
              {editing
                ? <textarea aria-label="Edit the draft message" value={text} onChange={(e) => setText(e.target.value)} rows={9} className="mono w-full rounded-md border border-line bg-[#FBFBFC] p-3 leading-relaxed" />
                : <div className="mono whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3 leading-relaxed" data-testid="draft-text">{text}</div>}
              {state ? (
                <div role="status" className="mt-3 flex items-center gap-2 font-bold text-teal" data-testid="decision-state"><CheckCircle2 size={16} aria-hidden /> {cap(state.decision)}{state.saved ? "" : " (undo available)"}</div>
              ) : (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button icon={<Pencil size={15} />} onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</Button>
                  <Button variant="primary" icon={<CheckCircle2 size={15} />} onClick={() => decide(text !== b.draft ? "edited" : "approved", text)}>Approve</Button>
                  <Button icon={<X size={15} />} onClick={() => decide("rejected", text)}>Reject</Button>
                </div>
              )}
            </>
          ) : (
            <p className="m-0 text-ink2">No message is needed for this action. {b.recommended_action === "verify quote" ? "Confirm the quote with the vendor before booking." : "You can proceed with the booking."}</p>
          )}
          <p className="mb-0 mt-2.5 text-[11.5px] text-ink2">Nothing is sent automatically. Approving records your decision in the audit log; a person sends the message.</p>
        </div>
      </Card>
    </div>
  );
}

export default function BookingCheck() {
  const { get: param, set } = useParams2();
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/api/meta/options"), staleTime: Infinity });
  const [form, setForm] = useState({ route: param("route", "AFBAN1"), vehicle_type: param("vehicle_type", "OB22"), vendor: param("vendor", "V002"), qty: param("qty", "3"), month: param("month", "3"), quoted: param("quoted", "20000") });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const resultRef = useRef<HTMLDivElement>(null);
  const run = useMutation({ mutationFn: (r: BookingRequest) => post<Booking>("/api/booking/check", r) });
  const upd = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const vts = useMemo(() => meta.data?.routes.find((r) => r.route === form.route)?.vehicle_types, [meta.data, form.route]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const err: Record<string, string> = {};
    if (!form.route.trim()) err.route = "Choose a route.";
    if (!/^[Vv]\d{3,}$/.test(form.vendor)) err.vendor = "Use a vendor code such as V002.";
    const qty = Number(form.qty);
    if (!(qty > 0 && qty <= 100)) err.qty = "Load must be between 0 and 100 MT.";
    if (form.quoted && !(Number(form.quoted) > 0)) err.quoted = "Enter a positive amount, or leave it empty.";
    setErrors(err);
    if (Object.keys(err).length) return;
    set({ route: form.route, vehicle_type: form.vehicle_type, vendor: form.vendor, qty: form.qty, month: form.month, quoted: form.quoted }, false);
    run.mutate({ route: form.route.trim().toUpperCase(), vehicle_type: form.vehicle_type, vendor: form.vendor.toUpperCase(), qty, month: Number(form.month), ...(form.quoted ? { quoted_rs: Number(form.quoted) } : {}) },
      { onSuccess: () => resultRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }) });
  };

  const a = run.data?.assumed_defaults;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[340px_1fr]">
      <Card>
        <CardHeader title="Planned booking" />
        <form onSubmit={submit} noValidate className="flex flex-col gap-3 p-4" aria-label="Planned booking">
          <Field id="route" label="Route" error={errors.route} hint="Route code, for example AFBAN1">
            <input id="route" list="routes" className={input} value={form.route} onChange={upd("route")} aria-invalid={!!errors.route} autoComplete="off" />
            <datalist id="routes">{meta.data?.routes.slice(0, 500).map((r) => <option key={r.route} value={r.route} />)}</datalist>
          </Field>
          <Field id="vt" label="Vehicle type" hint={a?.vehicle_capacity ? `Capacity ${a.vehicle_capacity} MT (assumed from vehicle type)` : undefined}>
            <select id="vt" className={input} value={form.vehicle_type} onChange={upd("vehicle_type")}>
              {(vts ?? meta.data?.vehicle_types.map((v) => v.vehicle_type) ?? [form.vehicle_type]).map((v) => <option key={v}>{v}</option>)}
            </select>
          </Field>
          <Field id="vendor" label="Vendor" error={errors.vendor}>
            <input id="vendor" list="vendors" className={input} value={form.vendor} onChange={upd("vendor")} aria-invalid={!!errors.vendor} autoComplete="off" />
            <datalist id="vendors">{meta.data?.vendors.map((v) => <option key={v.vendor_code} value={v.vendor_code} />)}</datalist>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="qty" label="Load (MT)" error={errors.qty}><input id="qty" inputMode="decimal" className={input} value={form.qty} onChange={upd("qty")} aria-invalid={!!errors.qty} /></Field>
            <Field id="month" label="Month">
              <select id="month" className={input} value={form.month} onChange={upd("month")}>{meta.data?.months.map((m) => <option key={m.month} value={m.month}>{m.label}</option>) ?? <option value={form.month}>{form.month}</option>}</select>
            </Field>
          </div>
          <Field id="quoted" label="Quoted price (optional)" error={errors.quoted} hint="Total for this movement">
            <input id="quoted" inputMode="numeric" className={input} value={form.quoted} onChange={upd("quoted")} aria-invalid={!!errors.quoted} placeholder="e.g. 20000" />
          </Field>
          {a?.distance != null && <Field id="dist" label="Distance (km)" hint="Assumed: typical for this route"><input id="dist" readOnly className={`${input} bg-[#FBFBFC] text-ink2`} value={Math.round(Number(a.distance))} /></Field>}
          <Button type="submit" variant="primary" className="h-[38px]" icon={<ClipboardCheck size={16} />} disabled={run.isPending}>{run.isPending ? "Checking…" : "Check booking"}</Button>
          <p className="m-0 text-[11.5px] text-ink2">Fields you leave out use the typical value for the route and are shown greyed out.</p>
        </form>
      </Card>
      <div ref={resultRef} className="min-w-0 scroll-mt-20">
        {run.isPending && <div className="flex flex-col gap-4" aria-busy="true"><Skeleton className="h-28" /><div className="grid gap-4 md:grid-cols-3"><Skeleton className="h-32" /><Skeleton className="h-32" /><Skeleton className="h-32" /></div><Skeleton className="h-48" /></div>}
        {run.isError && <Card><ErrorState message={run.error instanceof ApiError ? run.error.message : "Unexpected error"} onRetry={() => run.mutate(run.variables!)} /></Card>}
        {run.data && !run.isPending && <Result b={run.data} />}
        {!run.data && !run.isPending && !run.isError && (
          <Card><div className="px-6 py-12 text-center text-ink2"><ClipboardCheck className="mx-auto mb-2" size={28} aria-hidden /><div className="font-bold text-ink">Check a planned booking</div><div className="mt-1 text-xs">Fill in the booking on the left. You get the loss risk, a fair price, the under-load risk and one recommended action.</div></div></Card>
        )}
      </div>
    </div>
  );
}
