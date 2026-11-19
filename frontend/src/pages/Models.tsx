import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, Pill, Skeleton } from "@/components/ui";
import { get } from "@/lib/api";
import { humanise, num } from "@/lib/fmt";
import { QueryBoundary } from "@/lib/hooks";
import type { ModelCard } from "@/lib/types";

const fmtMetric = (n: string, v: number) => (/MAE/.test(n) ? num(v, 1) : v.toFixed(3));

export default function Models() {
  const q = useQuery({ queryKey: ["models"], queryFn: () => get<ModelCard[]>("/api/models"), staleTime: Infinity });
  return (
    <QueryBoundary query={q} skeleton={<div className="grid gap-4 lg:grid-cols-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-72" />)}</div>}>
      {(cards) => (
        <>
          <p className="m-0 text-ink2">Six models, each compared with two other algorithms and a naive baseline on a time-based test period.</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {cards.map((m) => (
              <Card key={m.task_id} data-testid={`model-${m.task_id}`}>
                <CardHeader title={`${m.task_id}: ${m.business_question}`} right={<Pill tone="neutral">{m.problem}</Pill>} />
                <div className="flex flex-col gap-3 p-4">
                  <div className="text-xs text-ink2">Predicts {humanise(m.target)} {"·"} test period {m.test_period} {"·"} {num(m.n_test)} test rows</div>
                  <div className="flex flex-wrap items-baseline gap-2"><span className="text-2xl font-bold">{fmtMetric(m.headline.metric, m.headline.winner_value)}</span><span className="text-xs text-ink2">{humanise(m.headline.metric)} for {m.winner}; baseline {fmtMetric(m.headline.baseline.metric, m.headline.baseline.value)}</span></div>
                  <table>
                    <caption className="sr-only">Algorithm comparison for {m.task_id}</caption>
                    <thead><tr><th scope="col">Model</th>{Object.keys(m.models[0].metrics).slice(0, 3).map((k) => <th key={k} scope="col" className="text-right">{humanise(k)}</th>)}</tr></thead>
                    <tbody>{m.models.map((x) => <tr key={x.name} className={x.name === m.winner ? "font-bold" : ""}><td>{x.name}{x.name === m.winner ? " (winner)" : ""}</td>{Object.entries(x.metrics).slice(0, 3).map(([k, v]) => <td key={k} className="num">{fmtMetric(k, v)}</td>)}</tr>)}</tbody>
                  </table>
                  <p className="m-0 text-xs text-ink2">{m.winner_reason}</p>
                  <div><h3 className="mb-1 text-[13px] font-bold">Top drivers</h3><ol className="m-0 pl-4 text-[12.5px]">{m.top_drivers.slice(0, 3).map((d) => <li key={d.feature}>{d.plain_english}</li>)}</ol></div>
                  {m.caveats.length > 0 && <p className="m-0 rounded border border-line bg-bg px-3 py-2 text-xs text-ink2"><b>Caveat:</b> {m.caveats[0]}</p>}
                  {m.figures.shap_bar && <a className="text-xs font-semibold text-navy" href={m.figures.shap_bar} target="_blank" rel="noreferrer">View SHAP chart (opens a new tab)</a>}
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </QueryBoundary>
  );
}
