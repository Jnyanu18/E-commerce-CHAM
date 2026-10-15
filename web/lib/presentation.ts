/**
 * Presentation-only arithmetic. This is NOT a business metric definition:
 * it expresses the change between two values the API already returned
 * (e.g. this week's and last week's `net_revenue`). Anything richer must
 * come from the API as a field.
 */
export function percentChange(current: number | null | undefined, previous: number | null | undefined): number | null {
  if (typeof current !== "number" || typeof previous !== "number") return null;
  if (!Number.isFinite(current) || !Number.isFinite(previous)) return null;
  if (previous === 0) return null;
  return ((current - previous) / Math.abs(previous)) * 100;
}
