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
  // Not yet in the canonical list: requested via contract change (see PROGRESS.md).
  | "ad_spend"
  | "platform_reported_revenue";

// `city_tier` is a proposed addition (enum in CONTRACTS section 8); see PROGRESS.md change requests.
export type GroupBy = "channel" | "sku" | "category" | "city" | "city_tier" | "pincode" | "payment_method" | "week";

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

/* ---- Intelligence (proposed endpoints; see PROGRESS.md contract change requests) ---- */

export type Severity = "low" | "medium" | "high" | "critical";

export interface AnomalyDriver {
  dimension: string; // e.g. "channel"
  key: string; // e.g. "meta"
  detail: string;
  /** Share of the anomaly attributed to this driver, 0..1, supplied by the engine. */
  share: number | null;
}

export interface AnomalyEvidence {
  call_id: string;
  description: string;
}

export interface Anomaly {
  id: string;
  detected_at: string; // UTC ISO
  severity: Severity;
  metric: string; // affected metric
  unit: MetricUnit;
  observed: number | null;
  expected: number | null;
  summary: string;
  drivers: AnomalyDriver[];
  evidence: AnomalyEvidence[];
}

export interface BriefAction {
  title: string;
  rationale: string;
  expected_impact_paise: number | null;
  ci_low_paise: number | null;
  ci_high_paise: number | null;
  /** Probability (0..1) the action beats the current plan, from the engine. */
  prob_beats_current: number | null;
  confidence: "low" | "medium" | "high";
  call_ids: string[];
}

export interface BriefStatement {
  text: string;
  call_ids: string[];
}

export interface BriefExperiment {
  name: string;
  hypothesis: string;
  design: string;
}

export type Brief =
  | {
      status: "ok";
      generated_at: string; // UTC ISO
      what_happened: BriefStatement[];
      why: BriefStatement[];
      actions: BriefAction[];
      experiment: BriefExperiment | null;
    }
  | { status: "INSUFFICIENT_DATA"; reason: string; fallback: string; generated_at: string | null };

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
  listIntegrations(orgId: string): Promise<Integration[]>;
  listSkuCogs(orgId: string): Promise<SkuCogs[]>;
  upsertSkuCogs(row: SkuCogs): Promise<SkuCogs>;
}
