// The only place where currency, units and dates are formatted. Components never hold a rupee literal.
const IN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const SYMBOL = "\u20B9";

export const num = (v: number | null | undefined, dp = 0): string =>
  v == null || Number.isNaN(v) ? "\u2013" : dp === 0 ? IN.format(v) : new Intl.NumberFormat("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp }).format(v);

/** Indian grouping: 123456 -> "₹1,23,456" */
export const inr = (v: number | null | undefined): string => (v == null || Number.isNaN(v) ? "\u2013" : `${v < 0 ? "-" : ""}${SYMBOL}${IN.format(Math.abs(v))}`);

/** Crore from 1e7, lakh from 1e5, otherwise grouped rupees: "₹4.5 Cr", "₹3.4 lakh", "₹12,345" */
export function money(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "\u2013";
  const a = Math.abs(v);
  const s = v < 0 ? "-" : "";
  if (a >= 1e7) return `${s}${SYMBOL}${(a / 1e7).toFixed(1)} Cr`;
  if (a >= 1e5) return `${s}${SYMBOL}${(a / 1e5).toFixed(1)} lakh`;
  return inr(v);
}

export const pct = (v: number | null | undefined, dp = 1): string => (v == null || Number.isNaN(v) ? "\u2013" : `${v.toFixed(dp)}%`);
export const prob = (v: number | null | undefined, dp = 0): string => (v == null ? "\u2013" : `${(v * 100).toFixed(dp)}%`);

export function mins(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "\u2013";
  const t = Math.round(v);
  const h = Math.floor(t / 60);
  const m = t % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} m` : `${m} m`;
}

const IST: Intl.DateTimeFormatOptions = { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false };
/** Exact time in IST, e.g. "09 Nov 2026, 14:05 IST". The API sends UTC ISO strings. */
export function istTime(iso: string | null | undefined): string {
  if (!iso) return "\u2013";
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return `${new Intl.DateTimeFormat("en-IN", IST).format(d)} IST`;
}

/** "6 min ago"; the exact IST time goes in the title attribute. */
export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "\u2013";
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
  const s = Math.max(0, Math.round((now - d) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** Formats one answer-card cell/placeholder by its catalogue unit: Rs -> crore/lakh money, % -> "69.4%", x -> "3.0×", count/number -> grouped. */
export function fmtByUnit(unit: string | null | undefined, v: unknown, dp = 1): string {
  if (v == null) return "–";
  if (typeof v !== "number") return String(v);
  if (unit === "Rs") return money(v);
  if (unit === "%") return pct(v, dp);
  if (unit === "x") return `${v.toFixed(dp)}×`;
  if (unit === "hours" || unit === "min") return num(v, dp);
  return num(v, Number.isInteger(v) ? 0 : dp);
}

const NUMBER_LIKE = new RegExp(`(${SYMBOL}[\\d,.]+(?:\\s?(?:Cr|lakh))?|\\d[\\d,]*(?:\\.\\d+)?%|\\d[\\d,]*(?:\\.\\d+)?×|\\b\\d[\\d,]*(?:\\.\\d+)?\\b)`, "g");
/** Splits an answer-card headline into text/number-looking segments. Every rupee-looking segment lives in this regex, not in the component. */
export const splitNumberLike = (text: string): string[] => text.split(NUMBER_LIKE);
export const looksLikeNumber = (part: string): boolean => new RegExp(`^[${SYMBOL}\\d]`).test(part) && /\d/.test(part);

export const monthLabel = (ym: string): string => new Date(`${ym}-01T00:00:00Z`).toLocaleString("en-IN", { month: "short", timeZone: "UTC" });
export const humanise = (s: string): string => s.replace(/_/g, " ");
export const cap = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);
