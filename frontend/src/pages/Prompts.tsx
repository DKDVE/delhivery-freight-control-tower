import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { Card, CardHeader, Drawer, Skeleton } from "@/components/ui";
import { QueryBoundary, useParams2 } from "@/lib/hooks";
import type { PromptCard } from "@/lib/types";

export default function Prompts() {
  const q = useQuery({ queryKey: ["prompts"], queryFn: () => get<PromptCard[]>("/api/prompts"), staleTime: Infinity });
  const { get: p, set } = useParams2();
  const open = p("t");
  return (
    <QueryBoundary query={q} skeleton={<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-40" />)}</div>}>
      {(cards) => {
        const sel = cards.find((c) => c.template_id === open);
        return (
          <>
            <p className="m-0 text-ink2">Ten message templates. The model writes only the wording; the numbers are filled in by code from the data.</p>
            <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-2 xl:grid-cols-3">
              {cards.map((c) => (
                <li key={c.template_id}>
                  <Card className="h-full">
                    <button type="button" onClick={() => set({ t: c.template_id }, false)} className="flex h-full w-full flex-col gap-1.5 p-4 text-left hover:bg-bg">
                      <span className="tag">Template {c.number}</span>
                      <span className="text-[15px] font-bold">{c.title}</span>
                      <span className="text-[12.5px] text-ink2">{c.purpose}</span>
                      <span className="mt-auto pt-2 text-xs font-semibold text-navy">{c.fields.length} input fields {"·"} view template</span>
                    </button>
                  </Card>
                </li>
              ))}
            </ul>
            {sel && (
              <Drawer open onClose={() => set({ t: null }, false)} title={sel.title} kicker={`Template ${sel.number}`}>
                <p className="m-0">{sel.purpose}</p>
                <section><h3 className="mb-1 text-sm font-bold">Input fields</h3><ul className="m-0 pl-4">{sel.fields.map((f) => <li key={f.name}><span className="mono font-bold">{f.name}</span> {"–"} {f.description}</li>)}</ul></section>
                <section><h3 className="mb-1 text-sm font-bold">Template</h3><pre className="mono m-0 whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3">{sel.template}</pre></section>
                <section><h3 className="mb-1 text-sm font-bold">Worked example</h3><div className="text-xs text-ink2">Input</div><pre className="mono m-0 whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3">{sel.example_input}</pre><div className="mt-2 text-xs text-ink2">Output</div><pre className="mono m-0 whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3">{sel.example_output}</pre></section>
                <section><CardHeader title="Known failure modes" /><ul className="m-0 pl-4 pt-2">{sel.failure_modes.map((f) => <li key={f}>{f}</li>)}</ul></section>
              </Drawer>
            )}
          </>
        );
      }}
    </QueryBoundary>
  );
}
