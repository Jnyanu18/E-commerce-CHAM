import type { Anomaly, Brief } from "@/lib/api/types";

/** Sample org ids understood by the mock layer. */
export const MOCK_ORG_ID = "sample-org";
export const MOCK_ORG_INSUFFICIENT = "sample-org-insufficient";

export const MOCK_ANOMALIES: Anomaly[] = [
  {
    id: "an-1",
    detected_at: "2026-10-13T03:30:00Z",
    severity: "critical",
    metric: "contribution_margin",
    unit: "paise",
    observed: 31_000_000,
    expected: 52_000_000,
    summary: "Weekly contribution margin fell sharply below the seasonal expectation.",
    drivers: [
      { dimension: "payment_method", key: "cod", detail: "COD return-to-origin losses rose in tier_3 cities", share: 0.55 },
      { dimension: "channel", key: "flipkart", detail: "Marketplace fees increased for the gifting category", share: 0.25 },
    ],
    evidence: [
      { call_id: "call_sample_001", description: "contribution_margin grouped by payment_method, last 7 days" },
      { call_id: "call_sample_002", description: "expected_rto_loss grouped by city_tier, last 7 days" },
    ],
  },
  {
    id: "an-2",
    detected_at: "2026-10-12T03:30:00Z",
    severity: "high",
    metric: "blended_cac",
    unit: "paise",
    observed: 84_000,
    expected: 61_000,
    summary: "Blended CAC is well above its recent range.",
    drivers: [{ dimension: "channel", key: "meta", detail: "Spend up with no matching rise in new customers", share: 0.7 }],
    evidence: [{ call_id: "call_sample_003", description: "ad_spend grouped by channel vs new customers" }],
  },
  {
    id: "an-3",
    detected_at: "2026-10-10T03:30:00Z",
    severity: "medium",
    metric: "net_revenue",
    unit: "paise",
    observed: 255_000_000,
    expected: 280_000_000,
    summary: "Net revenue dipped on Amazon after a stock-out.",
    drivers: [{ dimension: "sku", key: "SKU-SERUM-30", detail: "Out of stock for 3 days", share: null }],
    evidence: [{ call_id: "call_sample_004", description: "net_revenue grouped by sku, last 7 days" }],
  },
  {
    id: "an-4",
    detected_at: "2026-10-08T03:30:00Z",
    severity: "low",
    metric: "mer",
    unit: "ratio",
    observed: 8.9,
    expected: 9.4,
    summary: "MER slightly below its usual band.",
    drivers: [],
    evidence: [],
  },
];

export const MOCK_BRIEF: Brief = {
  status: "ok",
  generated_at: "2026-10-12T02:30:00Z",
  what_happened: [
    { text: "Contribution after marketing fell week over week, driven mainly by COD losses.", call_ids: ["call_sample_001"] },
    { text: "Meta spend rose while new-customer volume stayed flat.", call_ids: ["call_sample_003"] },
  ],
  why: [
    { text: "COD return-to-origin in tier_3 cities is the largest single driver of the margin drop.", call_ids: ["call_sample_002"] },
  ],
  actions: [
    {
      title: "Shift 10% of Meta prospecting budget to Google Search",
      rationale: "Marginal returns on Meta appear lower than Google at current spend (modelled, not platform-reported).",
      expected_impact_paise: 18_000_000,
      ci_low_paise: 4_000_000,
      ci_high_paise: 30_000_000,
      prob_beats_current: 0.72,
      confidence: "medium",
      call_ids: ["call_sample_005"],
    },
    {
      title: "Require prepaid for orders above Rs 2,500 in tier_3 pincodes",
      rationale: "High RTO share on large COD orders in these areas.",
      expected_impact_paise: 9_000_000,
      ci_low_paise: null,
      ci_high_paise: null,
      prob_beats_current: null,
      confidence: "low",
      call_ids: ["call_sample_002"],
    },
  ],
  experiment: {
    name: "Geo holdout: Meta prospecting",
    hypothesis: "Reducing Meta prospecting in a matched set of cities will not reduce net revenue.",
    design: "Hold out 6 matched cities for 4 weeks; measure with difference-in-differences on net revenue.",
  },
};

export const MOCK_BRIEF_INSUFFICIENT: Brief = {
  status: "INSUFFICIENT_DATA",
  reason: "47 weeks < 52",
  fallback: "descriptive",
  generated_at: "2026-10-12T02:30:00Z",
};
