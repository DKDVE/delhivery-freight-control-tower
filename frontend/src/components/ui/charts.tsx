import clsx from "clsx";
import {
  Line, LineChart, ResponsiveContainer, CartesianGrid, XAxis, YAxis, Tooltip, BarChart, Bar, Cell, LabelList,
  Scatter, ScatterChart, ZAxis, ReferenceLine,
} from "recharts";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { Kpi } from "@/lib/types";
import { money, pct, fmtByUnit } from "@/lib/fmt";
import type { AskColumn } from "@/lib/api";
import { Card } from "./primitives";

// Team palette (CLAUDE.md): navy for neutral series, red only for the highlighted/worst item, teal for good.
export const PALETTE = { red: "#EF4136", redDark: "#C21F26", ink: "#14202B", grey: "#5B6470", teal: "#1F6F78", navy: "#1F3A5F" };
export function Sparkline({ values, color = "#1F3A5F", width = 64, height = 28 }: { values: number[]; color?: string; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * (width - 4) + 2, height - 3 - ((v - lo) / span) * (height - 8)] as const);
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth={1.8} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r={2.6} fill={color} />
    </svg>
  );
}
export function KpiTile({ kpi, vs }: { kpi: Kpi; vs: string }) {
  const value = kpi.unit === "rs" ? money(kpi.value) : pct(kpi.value);
  const c = kpi.change_pct;
  let change: React.ReactNode = <span className="font-normal text-ink2">{kpi.note}</span>;
  if (c != null) {
    const up = c >= 0;
    const good = kpi.good_direction === "neutral" ? null : (up && kpi.good_direction === "up") || (!up && kpi.good_direction === "down");
    const Arrow = up ? ArrowUp : ArrowDown;
    change = (
      <>
        <span className={clsx("inline-flex items-center gap-0.5 font-semibold", good === null ? "text-ink2" : good ? "text-teal" : "text-red-dark")}>
          <Arrow size={12} aria-hidden />
          {Math.abs(c).toFixed(1)}{kpi.change_unit === "pp" ? " pts" : "%"}
          <span className="sr-only">{up ? " up" : " down"}{good === null ? "" : good ? ", favourable" : ", unfavourable"}</span>
        </span>
        <span className="text-ink2">{vs}</span>
      </>
    );
  }
  return (
    <Card className="flex min-w-0 flex-col gap-1 px-4 py-3.5" data-testid={`kpi-${kpi.id}`}>
      <div className="text-[12.5px] text-ink2">{kpi.label}</div>
      <div className="flex items-end justify-between gap-2">
        <div className="whitespace-nowrap text-2xl font-bold tracking-tight [font-variant-numeric:tabular-nums]">{value}</div>
        <Sparkline values={kpi.sparkline} color={kpi.good_direction === "up" ? "#1F6F78" : "#1F3A5F"} />
      </div>
      <div className="flex items-center gap-1.5 text-xs">{change}</div>
    </Card>
  );
}
const AXIS = { fontSize: 11, fill: "#5B6470" };
export function TrendChart({ data, xKey, yKey, unit = "%", height = 240, label }: { data: Record<string, string | number>[]; xKey: string; yKey: string; unit?: string; height?: number; label: string }) {
  return (
    <div role="img" aria-label={label} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 12, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#EDEFF2" vertical={false} />
          <XAxis dataKey={xKey} tick={AXIS} tickLine={false} axisLine={{ stroke: "#E3E6EA" }} interval="preserveStartEnd" />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} tickFormatter={(v) => `${Math.round(v)}${unit}`} width={44} />
          <Tooltip formatter={(v: number) => [`${v.toFixed(1)}${unit}`, "Margin"]} contentStyle={{ border: "1px solid #E3E6EA", borderRadius: 6, fontSize: 12, boxShadow: "none" }} />
          <Line type="monotone" dataKey={yKey} stroke="#1F3A5F" strokeWidth={2.2} dot={{ r: 3, fill: "#fff", stroke: "#1F3A5F", strokeWidth: 1.8 }} activeDot={{ r: 5 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
export function AnswerKpiTile({ label, value }: { label: string; value: string }) {
  return (
    <Card className="flex flex-col items-start gap-1 px-5 py-5">
      <div className="text-xs text-ink2">{label}</div>
      <div className="text-3xl font-bold tracking-tight [font-variant-numeric:tabular-nums]">{value}</div>
    </Card>
  );
}

/** One dimension + one % measure that sums (roughly) to a whole: a single 100%-wide stacked bar per row. Never a pie. */
export function Stacked100Bar({ data, labelKey, valueKey, label }: { data: Record<string, string | number>[]; labelKey: string; valueKey: string; label: string }) {
  return (
    <div role="img" aria-label={label} className="flex flex-col gap-2">
      {data.map((r, i) => {
        const v = Number(r[valueKey]) || 0;
        return (
          <div key={i} className="flex items-center gap-2 text-xs">
            <div className="w-32 shrink-0 truncate text-ink2" title={String(r[labelKey])}>{String(r[labelKey])}</div>
            <div className="h-4 flex-1 overflow-hidden rounded bg-grid">
              <div className="h-full rounded" style={{ width: `${Math.min(100, Math.max(0, v))}%`, background: i === 0 ? PALETTE.red : PALETTE.navy }} />
            </div>
            <div className="w-14 shrink-0 text-right font-semibold [font-variant-numeric:tabular-nums]">{v.toFixed(1)}%</div>
          </div>
        );
      })}
    </div>
  );
}

/** One dimension + 2 measures with different units: the first as a bar, the second as a dot (own scale, right-side value in the tooltip). */
export function BarDotChart({ data, labelKey, barKey, dotKey, barUnit, dotUnit, label, height }:
  { data: Record<string, string | number>[]; labelKey: string; barKey: string; dotKey: string; barUnit: string | null; dotUnit: string | null; label: string; height?: number }) {
  return (
    <div role="img" aria-label={label} style={{ height: height ?? data.length * 34 + 20 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 64, bottom: 0, left: 0 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey={labelKey} width={150} tick={{ fontSize: 12, fill: "#14202B" }} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v: number, name: string) => [fmtByUnit(name === barKey ? barUnit : dotUnit, v), name === barKey ? barKey : dotKey]}
                   contentStyle={{ border: "1px solid #E3E6EA", borderRadius: 6, fontSize: 12, boxShadow: "none" }} />
          <Bar dataKey={barKey} radius={3} barSize={12} isAnimationActive={false} fill={PALETTE.navy}>
            <LabelList dataKey={dotKey} position="right" formatter={(v: number) => fmtByUnit(dotUnit, v)} style={{ fontSize: 11, fontWeight: 600, fill: PALETTE.grey }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScatterXY({ data, xKey, yKey, label, height = 260 }: { data: Record<string, string | number>[]; xKey: string; yKey: string; label: string; height?: number }) {
  return (
    <div role="img" aria-label={label} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 12, right: 16, bottom: 4, left: -8 }}>
          <CartesianGrid stroke="#EDEFF2" />
          <XAxis type="number" dataKey={xKey} name={xKey} tick={{ fontSize: 11, fill: PALETTE.grey }} tickLine={false} axisLine={{ stroke: "#E3E6EA" }} />
          <YAxis type="number" dataKey={yKey} name={yKey} tick={{ fontSize: 11, fill: PALETTE.grey }} tickLine={false} axisLine={false} width={48} />
          <ZAxis range={[60, 60]} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} contentStyle={{ border: "1px solid #E3E6EA", borderRadius: 6, fontSize: 12, boxShadow: "none" }} />
          <Scatter data={data} fill={PALETTE.navy} isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export function answerChartUnit(columns: AskColumn[], key: string | null): string | null {
  return columns.find((c) => c.name === key)?.unit ?? null;
}

export function HBarChart({ data, labelKey, valueKey, unit, label, height }: { data: Record<string, string | number>[]; labelKey: string; valueKey: string; unit: string; label: string; height?: number }) {
  return (
    <div role="img" aria-label={label} style={{ height: height ?? data.length * 30 + 20 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 56, bottom: 0, left: 0 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey={labelKey} width={150} tick={{ fontSize: 12, fill: "#14202B" }} tickLine={false} axisLine={false} />
          <Bar dataKey={valueKey} radius={3} barSize={14} isAnimationActive={false}>
            {data.map((_, i) => <Cell key={i} fill="#EF4136" />)}
            <LabelList dataKey={valueKey} position="right" formatter={(v: number) => `${Math.round(v).toLocaleString("en-IN")}${unit}`} style={{ fontSize: 12, fontWeight: 600, fill: "#14202B" }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal bars of price per MT by vendor with a dashed line at the lane median. Cheapest teal, costly red, thin data grey, others navy. */
export function LaneBars({ data, median, label, format }: { data: { vendor: string; value: number; tag: string | null; thin: boolean }[]; median: number; label: string; format: (v: number) => string }) {
  const max = Math.max(median, ...data.map((d) => d.value)) * 1.12;
  return (
    <div role="img" aria-label={label} data-testid="lane-chart" style={{ height: data.length * 30 + 36 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 14, right: 64, bottom: 0, left: 0 }}>
          <XAxis type="number" hide domain={[0, max]} />
          <YAxis type="category" dataKey="vendor" width={52} tick={{ fontSize: 12, fill: PALETTE.ink }} tickLine={false} axisLine={false} />
          <ReferenceLine x={median} stroke={PALETTE.ink} strokeDasharray="4 3" label={{ value: `Lane median ${format(median)}`, position: "top", fontSize: 11, fill: PALETTE.grey }} />
          <Bar dataKey="value" radius={3} barSize={14} isAnimationActive={false}>
            {data.map((d, i) => <Cell key={i} fill={d.thin ? "#B5BAC2" : d.tag === "Cheapest" ? PALETTE.teal : d.tag === "Costly" ? PALETTE.red : PALETTE.navy} />)}
            <LabelList dataKey="value" position="right" formatter={(v: number) => format(v)} style={{ fontSize: 12, fontWeight: 600, fill: PALETTE.ink }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
