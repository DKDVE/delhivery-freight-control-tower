import { CheckCircle2, Pencil, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Card, CardHeader } from "@/components/ui";
import { cap } from "@/lib/fmt";
import { useDecision } from "@/lib/hooks";
import type { Draft } from "@/lib/types";

/** A drafted message with Edit, Approve and Reject. Decisions are optimistic with a 5 s undo and are only recorded, never sent. */
export function DraftPanel({ draft }: { draft: Draft }) {
  const { state, decide } = useDecision(draft.run_id);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft.text);
  useEffect(() => { setText(draft.text); setEditing(false); }, [draft.run_id, draft.text]);
  return (
    <Card data-testid="draft-panel">
      <CardHeader title={draft.title} sub={draft.mode === "DEMO" ? "rule-based draft" : "LLM draft"} />
      <div className="p-4">
        {editing
          ? <textarea aria-label="Edit the draft message" value={text} onChange={(e) => setText(e.target.value)} rows={9} className="mono w-full rounded-md border border-line bg-[#FBFBFC] p-3 leading-relaxed" />
          : <div className="mono whitespace-pre-wrap rounded-md border border-line bg-[#FBFBFC] p-3 leading-relaxed" data-testid="draft-text">{text}</div>}
        {state ? (
          <div role="status" className="mt-3 flex items-center gap-2 font-bold text-teal" data-testid="decision-state"><CheckCircle2 size={16} aria-hidden /> {cap(state.decision)}{state.saved ? "" : " (undo available)"}</div>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button icon={<Pencil size={15} />} onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</Button>
            <Button variant="primary" icon={<CheckCircle2 size={15} />} onClick={() => decide(text !== draft.text ? "edited" : "approved", text)}>Approve</Button>
            <Button icon={<X size={15} />} onClick={() => decide("rejected", text)}>Reject</Button>
          </div>
        )}
        <p className="mb-0 mt-2.5 text-[11.5px] text-ink2">{draft.note || "Nothing is sent automatically."}</p>
      </div>
    </Card>
  );
}
