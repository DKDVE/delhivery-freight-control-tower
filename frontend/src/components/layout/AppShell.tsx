import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BookOpen, Building2, ClipboardCheck, Cpu, FileText, LayoutDashboard, Menu, MessageSquare, PanelLeft, Route as RouteIcon, Search, Waypoints, ShieldCheck, Truck } from "lucide-react";
import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { get } from "@/lib/api";
import type { Health } from "@/lib/types";
import { Skeleton } from "@/components/ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, keys: "g o", end: true },
  { to: "/booking", label: "Booking check", icon: ClipboardCheck, keys: "g b" },
  { to: "/lanes", label: "Lane finder", icon: Waypoints, keys: "g f" },
  { to: "/vendors", label: "Vendors", icon: Truck, keys: "g v" },
  { to: "/trips", label: "Trips", icon: RouteIcon, keys: "g t" },
  { to: "/hubs", label: "Hubs", icon: Building2, keys: "g h" },
  { to: "/ask", label: "Ask the data", icon: MessageSquare, keys: "g a" },
  { to: "/prompts", label: "Prompt library", icon: BookOpen, keys: "g p" },
  { to: "/audit", label: "Audit log", icon: FileText, keys: "g l" },
  { to: "/models", label: "Model cards", icon: Cpu, keys: "g m" },
];
const GO: Record<string, string> = Object.fromEntries(NAV.map((n) => [n.keys.split(" ")[1], n.to]));

function titleFor(path: string): string {
  const hit = NAV.find((n) => (n.end ? path === n.to : path.startsWith(n.to)));
  if (path.startsWith("/trips/")) return "Trip detail";
  return hit?.label ?? "Freight Control Tower";
}

const stored = (): boolean => { try { return localStorage.getItem("sidebar-collapsed") === "1"; } catch { return false; } };

function NavList({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-0.5">
      {NAV.map(({ to, label, icon: Icon, keys, end }) => (
        <NavLink
          key={to} to={to} end={end} onClick={onNavigate} title={collapsed ? label : undefined}
          className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-md px-2.5 py-2 font-medium", isActive ? "bg-navy-tint font-bold text-navy" : "text-ink2 hover:bg-bg")}
        >
          <Icon size={16} aria-hidden className="flex-none" />
          {!collapsed && <span>{label}</span>}
          {!collapsed && <kbd className="ml-auto hidden rounded border border-b-2 border-line px-1.5 text-[11px] font-semibold text-ink2 lg:inline">{keys}</kbd>}
        </NavLink>
      ))}
    </nav>
  );
}

function Brand({ collapsed }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 px-2.5 pb-4 pt-1.5 text-[15px] font-bold tracking-tight">
      <span aria-hidden className="relative h-[22px] w-[22px] flex-none rounded-md bg-red after:absolute after:left-[5px] after:top-[9px] after:h-1 after:w-3 after:rounded-sm after:bg-white" />
      {!collapsed && "Freight Control Tower"}
    </div>
  );
}

export function AppShell() {
  const nav = useNavigate();
  const loc = useLocation();
  const [collapsed, setCollapsed] = useState(stored);
  const [sheet, setSheet] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const health = useQuery({ queryKey: ["health"], queryFn: () => get<Health>("/api/health"), staleTime: 60_000 });

  useEffect(() => { try { localStorage.setItem("sidebar-collapsed", collapsed ? "1" : "0"); } catch { /* private mode */ } }, [collapsed]);
  useEffect(() => { setSheet(false); document.title = `${titleFor(loc.pathname)} · Freight Control Tower`; }, [loc.pathname]);

  // keyboard: "/" focuses search, "g" then a letter navigates (g o, g b, g v, g t, g h, g a, g p, g l, g m)
  useEffect(() => {
    let armed = 0;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || t.closest("input, textarea, select, [contenteditable=true], [role=dialog]")) return;
      if (e.key === "/") { e.preventDefault(); searchRef.current?.focus(); return; }
      if (e.key === "g") { armed = Date.now(); return; }
      if (armed && Date.now() - armed < 1200 && GO[e.key]) { armed = 0; nav(GO[e.key]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nav]);

  const search = (q: string) => {
    const s = q.trim();
    if (!s) return;
    if (/^v\d{3,}$/i.test(s)) nav(`/vendors/${s.toUpperCase()}`);
    else if (/^trip-/i.test(s)) nav(`/trips/${s}`);
    else if (/^[A-Z]{2}[A-Z0-9]{3,5}$/.test(s.toUpperCase()) && !s.includes(" ")) nav(`/booking?route=${s.toUpperCase()}`);
    else nav(`/ask?q=${encodeURIComponent(s)}`);
    searchRef.current?.blur();
  };

  const h = health.data;
  const live = h?.mode === "LIVE";
  const sidebar = (c: boolean) => (<><Brand collapsed={c} /><NavList collapsed={c} onNavigate={() => setSheet(false)} /></>);

  return (
    <div className={clsx("grid min-h-screen grid-cols-[minmax(0,1fr)]", collapsed ? "md:grid-cols-[64px_minmax(0,1fr)]" : "md:grid-cols-[232px_minmax(0,1fr)]")}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-[70] focus:rounded focus:bg-surface focus:px-3 focus:py-2">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-line bg-surface p-2.5 md:flex">
        {sidebar(collapsed)}
        <div className="flex-1" />
        <button type="button" onClick={() => setCollapsed(!collapsed)} aria-pressed={collapsed} className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] text-ink2 hover:bg-bg">
          <PanelLeft size={16} aria-hidden /> {!collapsed && "Collapse"}<span className="sr-only">sidebar</span>
        </button>
      </aside>

      <Dialog.Root open={sheet} onOpenChange={setSheet}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30 md:hidden" />
          <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 left-0 z-50 w-[260px] border-r border-line bg-surface p-2.5 md:hidden">
            <Dialog.Title className="sr-only">Menu</Dialog.Title>
            {sidebar(false)}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <div className="flex min-w-0 flex-col">
        <div role="status" className="flex items-center gap-2 border-b border-line bg-navy-tint px-4 py-1.5 text-[12.5px] text-navy md:px-6">
          <ShieldCheck size={14} aria-hidden className="flex-none" />
          <span>{h?.banner ?? (live ? "Live mode: an LLM explains results; every number still comes from the models and data" : "Demo mode: explanations are rule-based; models and data are real")}</span>
        </div>
        <header className="sticky top-0 z-30 flex h-[52px] items-center gap-3 border-b border-line bg-surface px-4 md:h-14 md:px-6">
          <button type="button" className="md:hidden" aria-label="Open menu" onClick={() => setSheet(true)}><Menu size={20} aria-hidden /></button>
          <h1 className="text-[15px] font-bold tracking-tight md:text-[17px]">{titleFor(loc.pathname)}</h1>
          <form role="search" className="relative ml-auto hidden w-[300px] md:block" onSubmit={(e) => { e.preventDefault(); search(searchRef.current?.value ?? ""); }}>
            <Search size={15} aria-hidden className="pointer-events-none absolute left-2.5 top-[10px] text-ink2" />
            <input ref={searchRef} aria-label="Search vendors, routes, trips" placeholder="Search vendors, routes, trips" className="h-[34px] w-full rounded-md border border-line bg-bg pl-8 pr-8 text-[13px] placeholder:text-ink2" />
            <kbd className="absolute right-2 top-[7px] rounded border border-b-2 border-line bg-surface px-1.5 text-[11px] font-semibold text-ink2" aria-hidden>/</kbd>
          </form>
          <span data-testid="env-badge" className={clsx("ml-auto rounded-full border px-2 py-0.5 text-[11.5px] font-bold tracking-wide md:ml-0", live ? "border-[#b9d7da] bg-teal-tint text-teal" : "border-line bg-surface text-ink2")}>
            {h?.label ?? "…"}
          </span>
          <div aria-label="Signed-in user: project manager" className="grid h-[30px] w-[30px] place-items-center rounded-full bg-navy text-xs font-bold text-white">PM</div>
        </header>
        <main id="main" className="flex flex-1 flex-col gap-4 px-4 py-4 md:px-6 md:py-5">
          <Suspense fallback={<Skeleton className="h-64 w-full" />}><Outlet /></Suspense>
        </main>
        <footer className="border-t border-line bg-surface px-4 py-3.5 text-xs text-ink2 md:px-6">Student capstone prototype {"·"} not an official Delhivery system</footer>
      </div>
    </div>
  );
}

export const PageIntro = ({ children }: { children: ReactNode }) => <p className="m-0 text-ink2">{children}</p>;
export const Grid = ({ cols, className, children }: { cols: string; className?: string; children: ReactNode }) => <div className={clsx("grid gap-4", cols, className)}>{children}</div>;
