import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { ChevronDown, ChevronUp, Copy, Download, Eye, EyeOff, Pencil, RotateCcw } from "lucide-react";
import {
  AnswerKpiTile, BarDotChart, Button, Card, DataTable, EmptyState, ErrorState, GuardrailChip, HBarChart,
  ScatterXY, Skeleton, Stacked100Bar, TrendChart, answerChartUnit,
} from "@/components/ui";
import { rerunAsk, sendFeedback, type AskColumn, type AskResult, type ChartSpec, type FollowupItem } from "@/lib/api";
import { fmtByUnit, humanise, looksLikeNumber, splitNumberLike } from "@/lib/fmt";
import { PALETTE } from "@/components/ui/charts";

export interface AnswerCardState {
  id: number;
  question: string;
  streaming: boolean;
  analysis?: { query_id: string; params: Record<string, unknown>; interpretation: string; data_scope: string };
  result?: AskResult;
  chart?: ChartSpec;
  summary?: { text: string; source: "llm" | "template"; guardrail_status: "passed" | "replaced"; guardrail_events: string[]; mode: string };
  followups?: FollowupItem[];
  refusal?: { reason: string; closest: string[] };
  empty?: { filters_applied: Record<string, unknown>; notes?: string[] };
  error?: string;
  runId?: string | null;
}

function boldNumbers(text: string) {
  return splitNumberLike(text).map((p, i) => (looksLikeNumber(p) ? <strong key={i} className="[font-variant-numeric:tabular-nums]">{p}</strong> : <span key={i}>{p}</span>));
}

function ChartBlock({ chart, columns, rows, chartType, onChartType, visible, onToggleVisible }:
  { chart: ChartSpec; columns: AskColumn[]; rows: Record<string, unknown>[]; chartType: string; onChartType: (t: string) => void; visible: boolean; onToggleVisible: () => void }) {
  const unit = (k: string | null) => answerChartUnit(columns, k);
  const body = () => {
    if (chart.type === "kpi_tile") {
      const v = rows[0]?.[chart.y[0]];
      return <AnswerKpiTile label={chart.title} value={fmtByUnit(unit(chart.y[0]), v)} />;
    }
    if (chartType === "line") {
      return <TrendChart data={rows as Record<string, string | number>[]} xKey={chart.x!} yKey={chart.y[0]} unit={unit(chart.y[0]) === "%" ? "%" : ""} label={chart.title} />;
    }
    if (chartType === "stacked100") {
      return <Stacked100Bar data={rows as Record<string, string | number>[]} labelKey={chart.x!} valueKey={chart.y[0]} label={chart.title} />;
    }
    if (chartType === "bar_dot") {
      return <BarDotChart data={rows as Record<string, string | number>[]} labelKey={chart.x!} barKey={chart.y[0]} dotKey={chart.y[1]} barUnit={unit(chart.y[0])} dotUnit={unit(chart.y[1])} label={chart.title} />;
    }
    if (chartType === "scatter") {
      return <ScatterXY data={rows as Record<string, string | number>[]} xKey={chart.y[0]} yKey={chart.y[1]} label={chart.title} />;
    }
    if (chartType === "bar") {
      return <HBarChart data={rows as Record<string, string | number>[]} labelKey={chart.x!} valueKey={chart.y[0]} unit={unit(chart.y[0]) === "%" ? "%" : ""} label={chart.title} />;
    }
    return null; // "table": the table below is the whole card
  };
  const b = body();
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-bold text-ink">{chart.title}</div>
        <div className="flex items-center gap-1">
          {chart.valid_types.filter((t) => t !== "table").length > 1 && b && (
            <select value={chartType} onChange={(e) => onChartType(e.target.value)} className="h-7 rounded border border-line bg-surface px-1.5 text-xs" aria-label="Chart type">
              {chart.valid_types.filter((t) => t !== "table").map((t) => <option key={t} value={t}>{humanise(t)}</option>)}
            </select>
          )}
          {b && <Button size="sm" icon={visible ? <EyeOff size={13} /> : <Eye size={13} />} onClick={onToggleVisible}>{visible ? "Hide" : "Show"}</Button>}
        </div>
      </div>
      {b && visible && <Card className="p-4">{b}</Card>}
    </div>
  );
}

export function AnswerCard({ card, onFollowup, onEditQuestion }: { card: AnswerCardState; onFollowup: (q: string, qid: string, params: Record<string, unknown>) => void; onEditQuestion: (q: string) => void }) {
  const [chartType, setChartType] = useState<string | null>(null);
  const [chartVisible, setChartVisible] = useState(true);
  const [showAllRows, setShowAllRows] = useState(false);
  const [howOpen, setHowOpen] = useState(false);
  const [showQuery, setShowQuery] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackReasons, setFeedbackReasons] = useState<string[]>([]);
  const [feedbackNote, setFeedbackNote] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [live, setLive] = useState<{ result?: AskResult; chart?: ChartSpec; summary?: string } | null>(null);
  const [rerunning, setRerunning] = useState(false);

  const result = live?.result ?? card.result;
  const chart = live?.chart ?? card.chart;
  const summaryText = live?.summary ?? card.summary?.text;
  const effectiveType = chartType ?? chart?.type;

  const columns: ColumnDef<Record<string, unknown>, unknown>[] = useMemo(() => (result?.columns ?? []).map((c) => ({
    accessorKey: c.name, header: c.label,
    cell: (ctx) => <span className={c.role === "measure" ? "num [font-variant-numeric:tabular-nums]" : undefined}>{fmtByUnit(c.unit, ctx.getValue(), c.decimals)}</span>,
    meta: { align: c.role === "measure" ? "right" as const : undefined, sortKey: c.name },
  })), [result?.columns]);

  async function doRerun(params: Record<string, unknown>) {
    if (!card.runId) return;
    setRerunning(true);
    try {
      const r = await rerunAsk(card.runId, params);
      if (r.kind === "ok" && r.result && r.chart) { setLive({ result: r.result, chart: r.chart, summary: r.summary }); setChartType(null); }
    } finally { setRerunning(false); }
  }

  async function feedback(kind: "good" | "fix_it" | "request_review") {
    if (!card.runId) return;
    if (kind === "fix_it" && !feedbackOpen) { setFeedbackOpen(true); return; }
    await sendFeedback(card.runId, kind, feedbackReasons, feedbackNote || undefined);
    setFeedbackOpen(false);
    setToast("Thanks, recorded");
    setTimeout(() => setToast(null), 2500);
  }

  if (card.refusal) {
    return (
      <Card className="flex flex-col gap-3 p-5">
        <p className="m-0 font-semibold text-ink">I can&apos;t answer that from the approved data.</p>
        <div className="flex flex-wrap gap-2">{card.refusal.closest.map((q) => <Button key={q} size="sm" className="!h-auto !whitespace-normal py-1.5 text-left" onClick={() => onFollowup(q, "", {})}>{q}</Button>)}</div>
      </Card>
    );
  }
  if (card.empty) {
    return <Card className="p-5"><EmptyState title="No rows match these filters" hint={card.empty.notes?.join(" ") || undefined} action={<Button size="sm" icon={<RotateCcw size={13} />} onClick={() => onFollowup(card.question, card.analysis?.query_id ?? "", {})}>Reset filters</Button>} /></Card>;
  }
  if (card.error) {
    return <Card className="p-5"><ErrorState message={card.error} onRetry={() => onFollowup(card.question, "", {})} /></Card>;
  }
  if (card.streaming && !result) {
    return (
      <Card className="flex flex-col gap-3 p-5">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-24 w-full" />
      </Card>
    );
  }
  if (!result || !chart) return null;

  const visibleRows = showAllRows ? result.rows : result.rows.slice(0, 10);
  const totalRows = Math.max(result.total_rows ?? result.row_count, result.row_count);   // the table can be a top-N cut of a longer list
  const filterEntries = Object.entries(result.filters_applied ?? {});

  return (
    <Card className="flex flex-col gap-4 p-5">
      {summaryText && <p className="m-0 text-[15px] leading-6 text-ink" data-testid="headline">{boldNumbers(summaryText)}</p>}

      <ChartBlock chart={chart} columns={result.columns} rows={result.rows} chartType={effectiveType ?? chart.type} onChartType={setChartType} visible={chartVisible} onToggleVisible={() => setChartVisible((v) => !v)} />

      {chart.type !== "kpi_tile" && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <div className="text-xs text-ink2">{showAllRows && totalRows === result.row_count ? `All ${result.row_count} rows` : `Top ${showAllRows ? result.row_count : Math.min(10, result.row_count)} of ${totalRows} rows`}</div>
            <div className="flex gap-1.5">
              <Button size="sm" icon={<Copy size={13} />} onClick={() => navigator.clipboard?.writeText(JSON.stringify(result.rows, null, 2))}>Copy</Button>
              {card.runId && <Button size="sm" icon={<Download size={13} />} onClick={() => window.open(`/api/ask/runs/${card.runId}/csv`, "_blank")}>Download CSV</Button>}
            </div>
          </div>
          <div className="max-h-96 overflow-auto rounded-md border border-line">
            <DataTable data={visibleRows} columns={columns} caption="Answer table" getId={(r) => JSON.stringify(r)} loading={rerunning} />
          </div>
          {result.row_count > 10 && <Button size="sm" onClick={() => setShowAllRows((v) => !v)}>{showAllRows ? "Show top 10" : `Show all ${result.row_count} rows`}</Button>}
        </div>
      )}

      {filterEntries.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {filterEntries.map(([k, v]) => (
            <span key={k} className="inline-flex items-center gap-1 rounded-full border border-line bg-bg px-2.5 py-1 text-xs">
              {humanise(k)}: {String(v)}
              {k === "top" && typeof v === "number" && (
                <button type="button" className="ml-1 font-semibold text-navy" onClick={() => doRerun({ ...result.filters_applied, top: v + 10 })}>+10</button>
              )}
            </span>
          ))}
        </div>
      )}

      <div>
        <button type="button" className="flex items-center gap-1 text-xs font-semibold text-ink2 hover:text-ink" onClick={() => setHowOpen((v) => !v)}>
          {howOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />} How I got this
        </button>
        {howOpen && card.analysis && (
          <div className="mt-2 flex flex-col gap-2 rounded-md border border-line bg-bg p-3 text-xs">
            <div><span className="font-semibold">Understanding the question: </span>{card.analysis.interpretation}</div>
            <div><span className="font-semibold">Data used: </span>{card.analysis.data_scope}</div>
            {card.summary && <GuardrailChip status={card.summary.guardrail_status} label={card.summary.guardrail_status === "passed" ? "Passed" : "Rule-based answer used"} />}
            {card.summary && <div className="text-ink2">model route: {card.summary.mode}{card.summary.source === "template" ? " (template summary)" : ""}</div>}
            <button type="button" className="self-start font-semibold text-navy" onClick={() => setShowQuery((v) => !v)}>{showQuery ? "Hide query" : "Show query"}</button>
            {showQuery && (
              <div className="relative">
                <pre className="overflow-x-auto rounded bg-ink p-2 text-[11px] text-white">{result.sql_equivalent}</pre>
                <button type="button" className="absolute right-1.5 top-1.5 text-white/70 hover:text-white" onClick={() => navigator.clipboard?.writeText(result.sql_equivalent)} aria-label="Copy query"><Copy size={13} /></button>
              </div>
            )}
          </div>
        )}
      </div>

      {card.followups && card.followups.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {card.followups.map((f) => <Button key={f.question} size="sm" style={{ borderColor: PALETTE.navy }} onClick={() => onFollowup(f.question, f.query_id, f.params)}>{f.question}</Button>)}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <Button size="sm" onClick={() => feedback("good")}>Yes, correct</Button>
        <Button size="sm" onClick={() => feedback("fix_it")}>Fix it</Button>
        <Button size="sm" onClick={() => feedback("request_review")}>Request review</Button>
        <Button size="sm" icon={<Pencil size={12} />} className="ml-auto" onClick={() => onEditQuestion(card.question)}>Edit question</Button>
        {toast && <span role="status" className="text-xs font-semibold text-teal">{toast}</span>}
      </div>
      {feedbackOpen && (
        <div className="flex flex-col gap-2 rounded-md border border-line bg-bg p-3 text-xs">
          {["wrong_data", "wrong_chart", "unclear_wording", "other"].map((r) => (
            <label key={r} className="flex items-center gap-2">
              <input type="checkbox" checked={feedbackReasons.includes(r)} onChange={(e) => setFeedbackReasons((rs) => (e.target.checked ? [...rs, r] : rs.filter((x) => x !== r)))} />
              {humanise(r)}
            </label>
          ))}
          <textarea value={feedbackNote} onChange={(e) => setFeedbackNote(e.target.value)} placeholder="Optional note" className="rounded border border-line p-1.5 text-xs" rows={2} />
          <Button size="sm" variant="primary" className="self-start" onClick={() => feedback("fix_it")}>Submit</Button>
        </div>
      )}
    </Card>
  );
}
