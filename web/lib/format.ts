/**
 * Shared presentation formatters. Display only: no business definitions here.
 * Money is integer paise (INR). Timestamps are UTC ISO-8601, shown in IST.
 */

const DASH = "—";
const RUPEE = "₹";
export const IST_TIME_ZONE = "Asia/Kolkata";

const inrWhole = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});
const inrPaise = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const plainIndian = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Full amount with lakh/crore digit grouping, e.g. 123456700 paise -> "₹12,34,567". */
export function formatPaise(paise: number | null | undefined, opts: { showPaise?: boolean } = {}): string {
  if (!isNum(paise)) return DASH;
  return (opts.showPaise ? inrPaise : inrWhole).format(paise / 100).replace(/ /g, "");
}

/** Compact Indian units: plain below a lakh, L (lakh) and Cr (crore) above. */
export function formatPaiseCompact(paise: number | null | undefined): string {
  if (!isNum(paise)) return DASH;
  const rupees = paise / 100;
  const abs = Math.abs(rupees);
  const sign = rupees < 0 ? "-" : "";
  const trim = (n: number) => n.toFixed(2).replace(/\.?0+$/, "");
  if (abs >= 1e7) return `${sign}${RUPEE}${trim(abs / 1e7)} Cr`;
  if (abs >= 1e5) return `${sign}${RUPEE}${trim(abs / 1e5)} L`;
  return `${sign}${RUPEE}${plainIndian.format(abs)}`;
}

export function formatRatio(value: number | null | undefined, digits = 2): string {
  return isNum(value) ? `${value.toFixed(digits)}x` : DASH;
}

export function formatCount(value: number | null | undefined): string {
  return isNum(value) ? plainIndian.format(value) : DASH;
}

/** Percent from a number already in percent units (12.3 -> "+12.3%"). */
export function formatPercent(pct: number | null | undefined, opts: { signed?: boolean; digits?: number } = {}): string {
  if (!isNum(pct)) return DASH;
  const s = Math.abs(pct).toFixed(opts.digits ?? 1);
  if (Number(s) === 0) return `${s}%`;
  if (pct < 0) return `-${s}%`;
  return `${opts.signed ? "+" : ""}${s}%`;
}

/** Format a MetricResult value by its unit. */
export function formatByUnit(value: number | null | undefined, unit: string): string {
  switch (unit) {
    case "paise":
      return formatPaiseCompact(value);
    case "ratio":
      return formatRatio(value);
    case "count":
      return formatCount(value);
    default:
      return isNum(value) ? String(value) : DASH;
  }
}

/** UTC ISO timestamp -> "14 Oct 2026, 3:30 pm IST". */
export function formatIST(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const s = new Intl.DateTimeFormat("en-IN", {
    timeZone: IST_TIME_ZONE,
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(d);
  return `${s.replace(/ /g, " ")} IST`;
}

/** IST calendar date (YYYY-MM-DD) for an instant. */
export function toISTDateString(d: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: IST_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Shift a YYYY-MM-DD calendar date by whole days (no timezone involved). */
export function addDays(dateStr: string, days: number): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** IST calendar date string (YYYY-MM-DD) -> "14 Oct 2026". Never shifts by timezone. */
export function formatCalendarDate(dateStr: string | null | undefined): string {
  if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return DASH;
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" }).format(
    new Date(Date.UTC(y, m - 1, d)),
  );
}

/**
 * Parse a user-typed rupee amount ("1,234.50") to integer paise without float error.
 * Returns null for invalid input (negative, >2 decimals, non-numeric, empty).
 */
export function parseRupeesToPaise(input: string): number | null {
  const s = input.trim().replace(/,/g, "").replace(/^(?:₹|Rs\.?)\s*/i, "");
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(s);
  if (!m) return null;
  const paise = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0") || "0");
  return Number.isSafeInteger(paise) ? paise : null;
}

/** Integer paise -> plain rupee string for form inputs ("1234.50"). */
export function paiseToRupeeInput(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}
