import clsx from "clsx";
import { ChevronDown, ChevronRight, ShieldCheck, ShieldAlert } from "lucide-react";
import { useState, type ReactNode } from "react";

export function ChatMessage({ role, children }: { role: "user" | "assistant"; children: ReactNode }) {
  const user = role === "user";
  return (
    <div className={clsx("flex w-full max-w-[900px] gap-3", user && "flex-row-reverse self-end")}>
      <div aria-hidden className={clsx("grid h-7 w-7 flex-none place-items-center rounded-full text-[11px] font-bold", user ? "bg-navy-tint text-navy" : "bg-red text-white")}>{user ? "You" : "FC"}</div>
      <div className="min-w-0 flex-1">
        <div className={clsx("rounded-[10px] border px-3.5 py-2.5", user ? "ml-auto w-fit border-navy bg-navy text-white" : "border-line bg-surface")}>{children}</div>
      </div>
    </div>
  );
}

export function GuardrailChip({ status, label }: { status: "passed" | "replaced" | "refused"; label: string }) {
  const Icon = status === "replaced" ? ShieldAlert : ShieldCheck;
  const cls = status === "passed" ? "bg-teal-tint text-teal" : status === "replaced" ? "bg-amber-tint text-amber-text" : "bg-[#EEF0F3] text-ink2";
  return (
    <span className={clsx("inline-flex h-[22px] items-center gap-1 rounded-full px-2 text-xs font-bold", cls)} data-testid="guardrail-chip">
      <Icon size={13} aria-hidden /> Guardrail: {label}
    </span>
  );
}

/** Collapsible "How I got this": the tool calls and results behind an answer. */
export function ToolCallTrace({ steps, defaultOpen = false }: { steps: { kind: string; text: string }[]; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const Chev = open ? ChevronDown : ChevronRight;
  return (
    <div className="mt-2.5 rounded-md border border-line bg-[#FBFBFC]" data-testid="tool-trace">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[12.5px] font-semibold text-ink2">
        <Chev size={14} aria-hidden /> How I got this
      </button>
      {open && (
        <ol className="m-0 list-none space-y-0.5 border-t border-line px-2.5 py-2 pl-2.5 font-mono text-[11.5px] leading-relaxed">
          {steps.map((s, i) => (
            <li key={i}><span className="font-bold">{i + 1}. {s.kind}</span> {s.text}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
