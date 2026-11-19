import clsx from "clsx";
import { AlertTriangle, CheckCircle2, MinusCircle, Inbox, RefreshCw } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { prob } from "@/lib/fmt";
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost"; size?: "md" | "sm"; icon?: ReactNode };
export function Button({ variant = "secondary", size = "md", icon, className, children, ...rest }: BtnProps) {
  return (
    <button
      {...rest}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "md" ? "h-[34px] px-3.5 text-[13px]" : "h-7 px-2.5 text-xs",
        variant === "primary" && "border-red-dark bg-red-dark text-white hover:border-[#9E1A20] hover:bg-[#9E1A20]",
        variant === "secondary" && "border-line bg-surface text-ink hover:bg-bg",
        variant === "ghost" && "border-transparent bg-transparent text-navy hover:bg-navy-tint",
        className,
      )}
    >
      {icon}
      {children}
    </button>
  );
}
export function Card({ className, children, ...rest }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLElement>) {
  return <section {...rest} className={clsx("rounded border border-line bg-surface", className)}>{children}</section>;
}
export function CardHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-line px-4 py-3">
      <h2 className="text-sm font-bold">{title}</h2>
      {sub && <span className="text-[12.5px] text-ink2">{sub}</span>}
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}
const RISK = {
  high: { cls: "bg-red-tint text-red-dark", Icon: AlertTriangle, label: "High risk" },
  medium: { cls: "bg-amber-tint text-amber-text", Icon: MinusCircle, label: "Medium risk" },
  low: { cls: "bg-teal-tint text-teal", Icon: CheckCircle2, label: "Low risk" },
  neutral: { cls: "bg-navy-tint text-navy", Icon: MinusCircle, label: "Info" },
  grey: { cls: "bg-[#EEF0F3] text-ink2", Icon: MinusCircle, label: "" },
} as const;
export function RiskBadge({ level, label, hideIcon }: { level: keyof typeof RISK; label?: string; hideIcon?: boolean }) {
  const { cls, Icon, label: l } = RISK[level];
  return (
    <span className={clsx("inline-flex h-[22px] items-center gap-1 whitespace-nowrap rounded-full px-2 text-xs font-bold", cls)}>
      {!hideIcon && <Icon size={13} aria-hidden />}
      {label ?? l}
    </span>
  );
}
export const Pill = ({ children, tone = "grey" }: { children: ReactNode; tone?: keyof typeof RISK }) => <RiskBadge level={tone} label={String(children)} hideIcon />;
export function ProbabilityBar({ value, threshold, level, label }: { value: number; threshold?: number; level?: "low" | "medium" | "high"; label?: string }) {
  const lv = level ?? (threshold != null && value >= threshold ? "high" : "low");
  const col = lv === "high" ? "bg-red" : lv === "medium" ? "bg-amber" : "bg-teal";
  return (
    <div className="relative h-2 min-w-[90px] rounded bg-grid" role="img" aria-label={`${label ?? "Probability"} ${prob(value)}${threshold != null ? `, flag threshold ${prob(threshold)}` : ""}`}>
      <div className={clsx("absolute inset-y-0 left-0 rounded", col)} style={{ width: `${Math.min(100, value * 100)}%` }} />
      {threshold != null && <div className="absolute -inset-y-[3px] w-0.5 bg-ink" style={{ left: `${threshold * 100}%` }} />}
    </div>
  );
}
export const Skeleton = ({ className }: { className?: string }) => <div aria-hidden className={clsx("animate-pulse rounded bg-grid", className)} />;
export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center" role="status">
      <Inbox className="text-ink2" size={28} aria-hidden />
      <div className="font-bold">{title}</div>
      {hint && <div className="max-w-md text-xs text-ink2">{hint}</div>}
      {action}
    </div>
  );
}
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center" role="alert">
      <AlertTriangle className="text-red-dark" size={28} aria-hidden />
      <div className="font-bold">Something went wrong</div>
      <div className="max-w-md text-xs text-ink2">{message}</div>
      {onRetry && <Button size="sm" icon={<RefreshCw size={13} />} onClick={onRetry}>Retry</Button>}
    </div>
  );
}
export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-2 text-xs" aria-label="Progress">
      {steps.map((s, i) => (
        <li key={s} className="flex items-center gap-2" aria-current={i === current ? "step" : undefined}>
          <span className={clsx("grid h-5 w-5 place-items-center rounded-full border text-[11px] font-bold", i < current ? "border-teal bg-teal text-white" : i === current ? "border-navy bg-navy text-white" : "border-line text-ink2")}>
            {i < current ? "✓" : i + 1}
          </span>
          <span className={clsx(i === current ? "font-bold text-ink" : "text-ink2")}>{s}</span>
          {i < steps.length - 1 && <span className="h-px w-5 bg-line" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}
