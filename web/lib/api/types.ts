/** Types mirroring docs/CONTRACTS.md section 3 (MetricResult) and section 6 (recommendations). */

export type MetricUnit = "paise" | "ratio" | "count";
export type DataQualityStatus = "ok" | "degraded" | "insufficient";

/** Canonical metric names exposed by GET /metrics/{metric}. */
export type MetricName =
  | "net_revenue"
  | "contribution_margin"
  | "contribution_after_marketing"
  | "mer"
  | "blended_cac"
  | "platform_roas"
  | "expected_rto_loss"
    | "ad_spend"
  | "platform_reported_revenue"
  | "orders"
  | "aov"
  | "sessions"
  | "ad_impressions"
  | "ad_clicks"
  | "platform_reported_conversions";

// `city_tier` is a proposed addition (enum in CONTRACTS section 8); see PROGRESS.md change requests.
export type GroupBy = "channel" | "sku" | "category" | "city" | "city_tier" | "pincode" | "payment_method" | "day" | "week";

export type CityTier = "metro" | "tier_1" | "tier_2" | "tier_3" | "unknown";

export interface MetricGroup {
  key: string;
  value: number | null;
}

export interface DataQuality {
  status: DataQualityStatus;
  issues: string[];
  row_count: number;
}

export interface MetricResult {
  metric: string;
  value: number | null;
  unit: MetricUnit;
  currency: string;
  period: { start: string; end: string };
  filters: Record<string, string[]>;
  group_by: string | null;
  groups: MetricGroup[];
  definition: string;
  data_quality: DataQuality;
}

export interface MetricQuery {
  orgId: string;
  metric: MetricName;
  start: string; // IST calendar date YYYY-MM-DD
  end: string;
  groupBy?: GroupBy;
  filters?: Record<string, string[]>;
}

/* ---- Intelligence (shapes produced by engine/app/api.py; normalised in lib/api/normalize.ts) ---- */

export type Severity = "medium" | "high" | "critical";

/** A root-cause driver rendered defensively: a title plus whatever scalar fields the engine supplied. */
export interface RootCauseDriver {
  title: string;
  fields: Array<{ label: string; value: string }>;
}

export interface RootCause {
  /** Top-level scalar notes from the root_cause object (method, status, ...). */
  notes: Array<{ label: string; value: string }>;
  drivers: RootCauseDriver[];
}

export interface Anomaly {
  id: string;
  metric: string;
  date: string | null; // IST calendar day YYYY-MM-DD
  week_start: string | null;
  direction: "up" | "down" | null;
  severity: Severity | null;
  robust_z: number | null;
  observed: number | null;
  expected: number | null;
  /** null when the engine did not compute a root cause. */
  root_cause: RootCause | null;
}

/** A stored tool call; the brief's [cN] citations resolve to `call_id`. */
export interface BriefCall {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  result: unknown;
  error: unknown;
  timestamp: string | null;
}

export type Brief =
  | { status: "none"; reason: string }
  | {
      status: "ok";
      week_start: string;
      generated_at: string | null;
      emailed_at: string | null;
      mode: "narrative" | "fallback" | null;
      markdown: string;
      calls: BriefCall[];
    };

export type CheckSeverity = "ok" | "info" | "warn" | "error";

export interface DataQualityCheck {
  code: string;
  severity: CheckSeverity;
  count: number;
  message: string;
  examples: unknown[];
}

export interface DataQualityReport {
  org_id: string;
  generated_at: string | null;
  status: CheckSeverity;
  checks: DataQualityCheck[];
}

/* ---- Supabase-backed (read/write through the client; RLS enforced) ---- */

export type IntegrationProvider = "shopify" | "meta" | "google_ads" | "shiprocket" | "csv";
export type IntegrationStatus = "pending" | "active" | "error" | "disconnected";

/** Safe columns only: credentials_enc is never selectable from the browser. */
export interface Integration {
  id: string;
  org_id: string;
  provider: IntegrationProvider;
  account_ref: string;
  status: IntegrationStatus;
  last_synced_at: string | null;
  last_error: string | null;
  backfill_done: boolean;
}
export const INTEGRATION_SAFE_COLUMNS = "id,org_id,provider,account_ref,status,last_synced_at,last_error,backfill_done";

export interface SkuCogs {
  id?: string;
  org_id: string;
  sku: string;
  effective_from: string; // YYYY-MM-DD
  cogs_paise: number;
}

/** Everything the UI needs from a backend, real or mock. */
export interface Api {
  readonly isMock: boolean;
  getOrgId(): Promise<string | null>;
  getMetric(q: MetricQuery): Promise<MetricResult>;
  getAnomalies(orgId: string): Promise<Anomaly[]>;
  getBrief(orgId: string): Promise<Brief>;
  getDataQuality(orgId: string): Promise<DataQualityReport>;
  listIntegrations(orgId: string): Promise<Integration[]>;
  listSkuCogs(orgId: string): Promise<SkuCogs[]>;
  upsertSkuCogs(row: SkuCogs): Promise<SkuCogs>;
}
