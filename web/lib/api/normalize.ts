import { ApiError } from "./errors";
import type { Anomaly, Brief, BriefCall, CheckSeverity, DataQualityReport, MetricUnit, RootCause, RootCauseDriver, Severity } from "./types";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Scalar -> display string; nested values are summarised as JSON (never dropped silently). */
function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
const label = (k: string) => k.replace(/_/g, " ");

function fieldsOf(o: Obj, skip: string[]): Array<{ label: string; value: string }> {
  return Object.entries(o)
    .filter(([k, v]) => !skip.includes(k) && v !== null && v !== undefined && show(v) !== "")
    .map(([k, v]) => ({ label: label(k), value: show(v) }));
}

export function normalizeRootCause(raw: unknown): RootCause | null {
  if (!isObj(raw)) return null;
  const rawDrivers = Array.isArray(raw.drivers) ? raw.drivers : [];
  const drivers: RootCauseDriver[] = rawDrivers.filter(isObj).map((d, i) => {
    const title = str(d.driver) ?? str(d.name) ?? str(d.dimension) ?? `Driver ${i + 1}`;
    return { title, fields: fieldsOf(d, ["driver", "name"]) };
  });
  const notes = fieldsOf(raw, ["drivers"]).filter((f) => !f.value.startsWith("{") && !f.value.startsWith("["));
  if (drivers.length === 0 && notes.length === 0) return null;
  return { notes, drivers };
}

const SEVERITIES: Severity[] = ["medium", "high", "critical"];

export function normalizeAnomalies(body: unknown): Anomaly[] {
  const list = isObj(body) ? body.anomalies : null;
  if (!Array.isArray(list)) throw new ApiError("invalid_response", "anomalies response must be {anomalies: []}");
  const seen = new Set<string>();
  const out: Anomaly[] = [];
  list.filter(isObj).forEach((a, i) => {
    const metric = str(a.metric) ?? "unknown";
    const date = str(a.date);
    const id = str(a.id) ?? `${metric}:${date ?? i}`;
    if (seen.has(id)) return; // duplicate record
    seen.add(id);
    out.push({
      id,
      metric,
      date,
      week_start: str(a.week_start),
      direction: a.direction === "up" || a.direction === "down" ? a.direction : null,
      severity: SEVERITIES.includes(a.severity as Severity) ? (a.severity as Severity) : null,
      robust_z: num(a.robust_z),
      observed: num(a.observed),
      expected: num(a.expected),
      root_cause: normalizeRootCause(a.root_cause),
    });
  });
  return out;
}

export function normalizeBrief(body: unknown): Brief {
  if (!isObj(body)) throw new ApiError("invalid_response", "brief must be an object");
  if (body.status === "none") return { status: "none", reason: str(body.reason) ?? "No brief has been generated yet." };
  if (body.status !== "ok" || typeof body.markdown !== "string") throw new ApiError("invalid_response", "brief shape");
  const calls: BriefCall[] = (Array.isArray(body.calls) ? body.calls : []).filter(isObj).flatMap((c) =>
    typeof c.call_id === "string"
      ? [{
          call_id: c.call_id,
          tool: str(c.tool) ?? "unknown",
          args: isObj(c.args) ? c.args : {},
          ok: c.ok !== false,
          result: c.result ?? null,
          error: c.error ?? null,
          timestamp: str(c.timestamp),
        }]
      : [],
  );
  return {
    status: "ok",
    week_start: str(body.week_start) ?? "",
    generated_at: str(body.generated_at),
    emailed_at: str(body.emailed_at),
    mode: body.mode === "narrative" || body.mode === "fallback" ? body.mode : null,
    markdown: body.markdown,
    calls,
  };
}

const CHECK_SEV: CheckSeverity[] = ["ok", "info", "warn", "error"];
const sev = (v: unknown): CheckSeverity => (CHECK_SEV.includes(v as CheckSeverity) ? (v as CheckSeverity) : "warn");

export function normalizeDataQuality(body: unknown): DataQualityReport {
  if (!isObj(body) || !Array.isArray(body.checks)) throw new ApiError("invalid_response", "data-quality shape");
  return {
    org_id: str(body.org_id) ?? "",
    generated_at: str(body.generated_at),
    status: sev(body.status),
    checks: body.checks.filter(isObj).map((c) => ({
      code: str(c.code) ?? "unknown",
      severity: sev(c.severity),
      count: num(c.count) ?? 0,
      message: str(c.message) ?? "",
      examples: Array.isArray(c.examples) ? c.examples : [],
    })),
  };
}

const MONEY = new Set(["net_revenue", "contribution_margin", "contribution_after_marketing", "ad_spend", "platform_reported_revenue", "blended_cac", "aov", "expected_rto_loss"]);
const RATIO = new Set(["mer", "platform_roas"]);
/** Display unit for a metric name when only a bare number is available (anomaly observed/expected). */
export function unitForMetric(metric: string): MetricUnit {
  return MONEY.has(metric) ? "paise" : RATIO.has(metric) ? "ratio" : "count";
}
