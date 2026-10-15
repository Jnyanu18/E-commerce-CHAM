import { ApiError } from "./errors";
import type { DataQuality, MetricGroup, MetricResult } from "./types";

const UNITS = ["paise", "ratio", "count"];
const STATUSES = ["ok", "degraded", "insufficient"];

function bad(msg: string): never {
  throw new ApiError("invalid_response", `Invalid MetricResult: ${msg}`);
}
function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function numOrNull(v: unknown, name: string): number | null {
  if (v === null) return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return bad(`${name} must be a finite number or null`);
}

/** Runtime validation of the MetricResult contract. Rejects malformed or duplicate-keyed payloads. */
export function parseMetricResult(raw: unknown): MetricResult {
  if (!isObj(raw)) return bad("not an object");
  if (typeof raw.metric !== "string" || !raw.metric) bad("metric");
  if (typeof raw.unit !== "string" || !UNITS.includes(raw.unit)) bad("unit");
  if (typeof raw.currency !== "string") bad("currency");
  if (typeof raw.definition !== "string") bad("definition");
  const value = numOrNull(raw.value, "value");
  if (raw.unit === "paise" && value !== null && !Number.isInteger(value)) bad("paise value must be an integer");

  const period = raw.period;
  if (!isObj(period) || typeof period.start !== "string" || typeof period.end !== "string") bad("period");

  const dq = raw.data_quality;
  if (!isObj(dq) || typeof dq.status !== "string" || !STATUSES.includes(dq.status)) bad("data_quality.status");
  if (!Array.isArray((dq as Record<string, unknown>).issues) || !((dq as Record<string, unknown>).issues as unknown[]).every((i) => typeof i === "string")) {
    bad("data_quality.issues");
  }
  if (typeof (dq as Record<string, unknown>).row_count !== "number") bad("data_quality.row_count");

  const rawGroups = raw.groups ?? [];
  if (!Array.isArray(rawGroups)) bad("groups");
  const seen = new Set<string>();
  const groups: MetricGroup[] = (rawGroups as unknown[]).map((g, i) => {
    if (!isObj(g) || typeof g.key !== "string") return bad(`groups[${i}].key`);
    if (seen.has(g.key)) bad(`duplicate group key "${g.key}"`);
    seen.add(g.key);
    return { key: g.key, value: numOrNull(g.value, `groups[${i}].value`) };
  });

  const filters = isObj(raw.filters) ? (raw.filters as Record<string, string[]>) : {};
  if (raw.group_by !== null && raw.group_by !== undefined && typeof raw.group_by !== "string") bad("group_by");

  return {
    metric: raw.metric as string,
    value,
    unit: raw.unit as MetricResult["unit"],
    currency: raw.currency as string,
    period: { start: period.start as string, end: period.end as string },
    filters,
    group_by: (raw.group_by as string | null | undefined) ?? null,
    groups,
    definition: raw.definition as string,
    data_quality: dq as unknown as DataQuality,
  };
}
