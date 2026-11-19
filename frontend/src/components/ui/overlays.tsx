import * as Dialog from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as ToastPrimitive from "@radix-ui/react-toast";
import clsx from "clsx";
import { X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
export function Drawer({ open, onClose, title, kicker, badge, footer, children }: { open: boolean; onClose: () => void; title: string; kicker?: string; badge?: ReactNode; footer?: ReactNode; children: ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30" />
        <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[470px] flex-col border-l border-line bg-surface">
          <div className="flex items-center gap-2.5 border-b border-line px-[18px] py-3.5">
            <div className="min-w-0">
              {kicker && <div className="tag">{kicker}</div>}
              <Dialog.Title className="truncate text-lg font-bold">{title}</Dialog.Title>
            </div>
            {badge}
            <Dialog.Close className="ml-auto inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs font-semibold text-navy hover:bg-navy-tint" aria-label="Close panel">
              <X size={16} aria-hidden /> Esc
            </Dialog.Close>
          </div>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-[18px] py-4">{children}</div>
          {footer && <div className="flex flex-wrap gap-2 border-t border-line px-[18px] py-3">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Tabs({ value, onValueChange, items, label }: { value: string; onValueChange: (v: string) => void; items: { value: string; label: ReactNode }[]; label: string }) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange}>
      <TabsPrimitive.List aria-label={label} className="inline-flex gap-1.5">
        {items.map((i) => (
          <TabsPrimitive.Trigger
            key={i.value}
            value={i.value}
            className={clsx("inline-flex h-7 items-center rounded-full border px-3 text-[12.5px]", "data-[state=active]:border-[#c5cfdd] data-[state=active]:bg-navy-tint data-[state=active]:font-semibold data-[state=active]:text-navy", "data-[state=inactive]:border-line data-[state=inactive]:bg-surface hover:bg-bg")}
          >
            {i.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
    </TabsPrimitive.Root>
  );
}
interface ToastOpts { title: string; description?: string; actionLabel?: string; onAction?: () => void; duration?: number; tone?: "ok" | "error" }
const ToastCtx = createContext<(t: ToastOpts) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<(ToastOpts & { id: number; open: boolean })[]>([]);
  const push = useCallback((t: ToastOpts) => setItems((s) => [...s, { ...t, id: Date.now() + Math.random(), open: true }]), []);
  const ctx = useMemo(() => push, [push]);
  return (
    <ToastCtx.Provider value={ctx}>
      <ToastPrimitive.Provider swipeDirection="right" label="Notifications">
        {children}
        {items.map((t) => (
          <ToastPrimitive.Root
            key={t.id}
            open={t.open}
            duration={t.duration ?? 5000}
            onOpenChange={(o) => !o && setItems((s) => s.filter((x) => x.id !== t.id))}
            className={clsx("flex items-center gap-3 rounded border bg-surface px-4 py-3 text-[13px]", t.tone === "error" ? "border-red" : "border-line")}
          >
            <div className="min-w-0 flex-1">
              <ToastPrimitive.Title className="font-bold">{t.title}</ToastPrimitive.Title>
              {t.description && <ToastPrimitive.Description className="text-xs text-ink2">{t.description}</ToastPrimitive.Description>}
            </div>
            {t.actionLabel && (
              <ToastPrimitive.Action altText={t.actionLabel} asChild>
                <button type="button" onClick={t.onAction} className="rounded-md border border-line px-2.5 py-1 text-xs font-bold text-navy hover:bg-navy-tint">{t.actionLabel}</button>
              </ToastPrimitive.Action>
            )}
            <ToastPrimitive.Close aria-label="Dismiss" className="text-ink2 hover:text-ink"><X size={14} aria-hidden /></ToastPrimitive.Close>
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed bottom-4 right-4 z-[60] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2 outline-none" />
      </ToastPrimitive.Provider>
    </ToastCtx.Provider>
  );
}
