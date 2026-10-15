import type { Anomaly, Brief, BriefCall, DataQualityReport } from "@/lib/api/types";

/** Sample org ids understood by the mock layer (scenario is chosen via the org id). */
export const MOCK_ORG_ID = "sample-org";
export const MOCK_ORG_NO_BRIEF = "sample-org-nobrief";
export const MOCK_ORG_FALLBACK = "sample-org-fallback";
export const MOCK_ORG_INSUFFICIENT = "sample-org-insufficient";

export const MOCK_ANOMALIES: Anomaly[] = [
  {
    id: "an-1", metric: "contribution_margin", date: "2026-10-12", week_start: "2026-10-12", direction: "down", severity: "critical",
    robust_z: -4.2, observed: 31_000_000, expected: 52_000_000,
    root_cause: {
      notes: [{ label: "method", value: "LMDI-I (log-mean Divisia), exact" }],
      drivers: [
        { title: "rto_rate", fields: [{ label: "change", value: "+4.1 pp" }, { label: "evidence", value: "expected_rto_loss up in tier_3 COD" }, { label: "confidence", value: "medium: 3 weeks of history" }] },
        { title: "marketplace_fees", fields: [{ label: "change", value: "+1.2 pp" }, { label: "confidence", value: "low: fees are estimated" }] },
      ],
    },
  },
  { id: "an-2", metric: "blended_cac", date: "2026-10-11", week_start: "2026-10-05", direction: "up", severity: "high", robust_z: 3.4, observed: 84_000, expected: 61_000, root_cause: null },
  { id: "an-3", metric: "net_revenue", date: "2026-10-09", week_start: "2026-10-05", direction: "down", severity: "medium", robust_z: -2.7, observed: 255_000_000, expected: 280_000_000, root_cause: null },
  { id: "an-4", metric: "mer", date: "2026-10-02", week_start: "2026-09-28", direction: "down", severity: "medium", robust_z: -2.5, observed: 8.9, expected: 9.4, root_cause: null },
];

function metricCall(id: string, metric: string, value: number, unit: string): BriefCall {
  return {
    call_id: id, tool: "get_metric", ok: true, error: null, timestamp: "2026-10-12T02:29:00Z",
    args: { metric, start: "2026-10-05", end: "2026-10-11", filters: {} },
    result: { metric, value, unit, currency: "INR", period: { start: "2026-10-05", end: "2026-10-11" }, data_quality: { status: "ok", issues: [], row_count: 560 } },
  };
}

const CALLS: BriefCall[] = [
  metricCall("c1", "net_revenue", 280_000_000, "paise"),
  metricCall("c2", "contribution_after_marketing", 31_000_000, "paise"),
  metricCall("c3", "mer", 8.9, "ratio"),
  {
    call_id: "c4", tool: "get_recommendations", ok: true, error: null, timestamp: "2026-10-12T02:29:30Z", args: {},
    result: { status: "INSUFFICIENT_DATA", reason: "47 weeks < 52", fallback: "descriptive" },
  },
];

const MARKDOWN = [
  "# Weekly brief",
  "",
  "## What happened",
  "Net revenue was ₹2.8 Cr for the week [c1]. Contribution after marketing was ₹31 L [c2] and MER was 8.9x [c3].",
  "",
  "## Why",
  "Contribution fell mainly because of **COD return-to-origin losses**; see the anomaly feed for drivers.",
  "",
  "## Top 3 actions",
  "No budget recommendation is made: the model has insufficient data (47 weeks < 52) [c4]. Descriptive analysis only.",
  "",
  "## Experiment",
  "- Geo holdout of Meta prospecting would give a causal read once enough history exists [c4].",
].join("\n");

export const MOCK_BRIEF: Brief = {
  status: "ok", week_start: "2026-10-05", generated_at: "2026-10-12T02:30:00Z", emailed_at: "2026-10-12T02:31:00Z",
  mode: "narrative", markdown: MARKDOWN, calls: CALLS,
};

export const MOCK_BRIEF_FALLBACK: Brief = {
  ...MOCK_BRIEF, mode: "fallback", emailed_at: null,
  markdown: [
    "# Weekly brief (data only)",
    "",
    "- Net revenue ₹2.8 Cr [c1]",
    "- Contribution after marketing ₹31 L [c2]",
    "- MER 8.9x [c3]",
    "- Recommendations: insufficient data [c4]",
  ].join("\n"),
};

export const MOCK_BRIEF_NONE: Brief = { status: "none", reason: "no brief has been generated for this brand yet" };

export const MOCK_BRIEF_INSUFFICIENT: Brief = {
  status: "ok", week_start: "2026-10-05", generated_at: "2026-10-12T02:30:00Z", emailed_at: null, mode: "narrative",
  markdown: [
    "# Weekly brief",
    "",
    "## What happened",
    "Insufficient data: tracked history is too short for any causal statement [c1]. No recommendation, interval or confidence is given.",
  ].join("\n"),
  calls: [{ ...CALLS[3], call_id: "c1" }],
};

export const MOCK_DATA_QUALITY: DataQualityReport = {
  org_id: MOCK_ORG_ID, generated_at: "2026-10-14T20:00:00Z", status: "error",
  checks: [
    { code: "missing_sku_lines", severity: "error", count: 12, message: "order lines with no SKU; COGS is unknown for them", examples: ["ord-1041", "ord-1090"] },
    { code: "skus_not_in_catalogue", severity: "warn", count: 2, message: "SKUs on orders that are not in the skus table", examples: ["SKU-NEW-1", "SKU-NEW-2"] },
    { code: "stale_integration", severity: "warn", count: 1, message: "integration not synced in over 36 hours", examples: [{ provider: "google_ads" }] },
    { code: "unreconciled_settlements", severity: "info", count: 3, message: "settlements not yet matched to orders", examples: [] },
    { code: "possible_duplicate_orders", severity: "ok", count: 0, message: "ok", examples: [] },
  ],
};
