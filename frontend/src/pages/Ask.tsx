import { Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerCard, type AnswerCardState } from "@/components/AnswerCard";
import { Button, Card, ChatMessage } from "@/components/ui";
import { ApiError, streamAskCard, type AskHistoryTurn } from "@/lib/api";
import { useParams2 } from "@/lib/hooks";

const EXAMPLES: { group: string; qs: string[] }[] = [
  { group: "Vendors", qs: ["Which vendors lose the most money?", "Which lanes have the highest overpayment against the fair price?"] },
  { group: "Margin", qs: ["How has margin trended by month?", "What is the margin % by zone?"] },
  { group: "Middle mile", qs: ["Which hubs are slowest at handling?", "Which corridors overrun their planned time the most?"] },
];

export default function Ask() {
  const { get: p, set } = useParams2();
  const [cards, setCards] = useState<AnswerCardState[]>([]);
  const [text, setText] = useState(p("q"));
  const busy = cards.some((c) => c.streaming);
  const endRef = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const started = useRef(false);
  const history = useRef<AskHistoryTurn[]>([]);

  const patch = (id: number, f: (c: AnswerCardState) => AnswerCardState) => setCards((cs) => cs.map((c) => (c.id === id ? f(c) : c)));

  const ask = useCallback(async (question: string) => {
    const q = question.trim();
    if (!q) return;
    const id = Date.now();
    setCards((cs) => [...cs, { id, question: q, streaming: true }]);
    setText("");
    abort.current = new AbortController();
    try {
      await streamAskCard(q, history.current, (e) => {
        if (e.event === "analysis") { patch(id, (c) => ({ ...c, analysis: e.data })); history.current = [...history.current, { question: q, query_id: e.data.query_id, params: e.data.params }].slice(-3); }
        else if (e.event === "result") patch(id, (c) => ({ ...c, result: e.data }));
        else if (e.event === "chart") patch(id, (c) => ({ ...c, chart: e.data }));
        else if (e.event === "summary") patch(id, (c) => ({ ...c, summary: e.data }));
        else if (e.event === "followups") patch(id, (c) => ({ ...c, followups: e.data.items }));
        else if (e.event === "refusal") patch(id, (c) => ({ ...c, refusal: e.data }));
        else if (e.event === "empty") patch(id, (c) => ({ ...c, empty: e.data }));
        else if (e.event === "error") patch(id, (c) => ({ ...c, error: e.data.message }));
        else if (e.event === "done") patch(id, (c) => ({ ...c, runId: e.data.run_id }));
      }, abort.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") patch(id, (c) => ({ ...c, error: err instanceof ApiError ? err.message : "Could not reach the server." }));
    } finally {
      patch(id, (c) => ({ ...c, streaming: false }));
    }
  }, []);

  const followup = useCallback((question: string, _qid: string, _params: Record<string, unknown>) => void ask(question), [ask]);
  const editQuestion = useCallback((question: string) => setText(question), []);

  // /ask?q=... from the global search runs once
  useEffect(() => {
    const q = p("q");
    if (q && !started.current) { started.current = true; void ask(q); set({ q: null }, false); }
  }, [p, ask, set]);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [cards]);

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex flex-1 flex-col gap-4" aria-live="polite" aria-label="Conversation">
        {cards.length === 0 && (
          <Card className="px-6 py-10 text-center">
            <div className="text-base font-bold">Ask a question about the freight data</div>
            <p className="mx-auto mb-4 mt-1 max-w-xl text-xs text-ink2">Answers come from fixed queries on the real data: a chart, a table and a plain-English summary with no invented numbers.</p>
            <div className="mx-auto flex max-w-2xl flex-col gap-3 text-left">
              {EXAMPLES.map((g) => (
                <div key={g.group}>
                  <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-ink2">{g.group}</div>
                  <div className="flex flex-wrap gap-2">{g.qs.map((s) => <Button key={s} size="sm" className="!h-auto !whitespace-normal py-1.5 text-left" onClick={() => void ask(s)}>{s}</Button>)}</div>
                </div>
              ))}
            </div>
          </Card>
        )}
        {cards.map((c) => (
          <div key={c.id} className="flex flex-col gap-3">
            <ChatMessage role="user">
              <span className="flex items-center gap-2">{c.question}</span>
            </ChatMessage>
            <AnswerCard card={c} onFollowup={followup} onEditQuestion={editQuestion} />
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form className="sticky bottom-0 flex gap-2 border-t border-line bg-bg py-3" onSubmit={(e) => { e.preventDefault(); void ask(text); }} aria-label="Ask a question">
        <label htmlFor="ask" className="sr-only">Your question</label>
        <input id="ask" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} placeholder="e.g. Which vendor lost the most money last quarter?" className="h-10 flex-1 rounded-md border border-[#CBD0D6] bg-surface px-3 text-sm" autoComplete="off" />
        <Button type="submit" variant="primary" className="h-10" icon={<Send size={15} />} disabled={busy || !text.trim()}>{busy ? "Answering…" : "Ask"}</Button>
      </form>
    </div>
  );
}
