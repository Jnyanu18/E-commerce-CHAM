import type { GroupBy, MetricName, MetricQuery, MetricResult, MetricUnit } from "@/lib/api/types";

/**
 * Sample MetricResult fixtures for a 30-day period (paise). These are hand-written
 * stand-ins for the metric layer; the numbers are NOT derived from each other in code.
 * Selected only via NEXT_PUBLIC_USE_MOCKS and always labelled "Sample data" in the UI.
 */
interface Fixture {
  unit: MetricUnit;
  definition: string;
  value: number;
  /** Group fixtures keyed by group_by. */
  groups: Partial<Record<GroupBy | "city_tier", Array<[string, number]>>>;
  /** Fixture-specific data-quality issues for a given group_by (or "*" for all). */
  issues?: Record<string, string[]>;
}

const M = 1_000_000; // 1M paise = Rs 10,000

const FIXTURES: Partial<Record<MetricName, Fixture>> = {
  net_revenue: {
    unit: "paise",
    definition: "gross sales - discounts - refunds (sample definition text)",
    value: 1200 * M,
    groups: {
      channel: [["shopify", 640 * M], ["amazon", 330 * M], ["flipkart", 150 * M], ["blinkit", 80 * M]],
      payment_method: [["prepaid", 760 * M], ["cod", 440 * M]],
      sku: [["SKU-SERUM-30", 380 * M], ["SKU-CREAM-50", 300 * M], ["SKU-SOAP-3PK", 210 * M], ["SKU-OIL-100", 190 * M], ["SKU-KIT-GIFT", 120 * M]],
    },
  },
  contribution_margin: {
    unit: "paise",
    definition: "net_revenue - COGS - fees - shipping - RTO/return cost (sample definition text)",
    value: 276 * M,
    groups: {
      channel: [["shopify", 190 * M], ["amazon", 52 * M], ["flipkart", 14 * M], ["blinkit", 20 * M]],
      sku: [["SKU-SERUM-30", 96 * M], ["SKU-CREAM-50", 82 * M], ["SKU-SOAP-3PK", 48 * M], ["SKU-OIL-100", 36 * M], ["SKU-KIT-GIFT", 14 * M]],
      category: [["skincare", 170 * M], ["haircare", 70 * M], ["gifting", 36 * M]],
      city: [["Mumbai", 52 * M], ["Delhi NCR", 44 * M], ["Bengaluru", 40 * M], ["Hyderabad", 30 * M], ["Pune", 24 * M], ["Other", 86 * M]],
      city_tier: [["metro", 120 * M], ["tier_1", 80 * M], ["tier_2", 52 * M], ["tier_3", 18 * M], ["unknown", 6 * M]],
      payment_method: [["prepaid", 190 * M], ["cod", 86 * M]],
    },
    issues: { sku: ["cogs_missing_for_skus: ['SKU-KIT-GIFT']"] },
  },
  contribution_after_marketing: {
    unit: "paise",
    definition: "contribution_margin - ad spend (sample definition text)",
    value: 150 * M,
    groups: {
      channel: [["shopify", 96 * M], ["amazon", 38 * M], ["flipkart", -6 * M], ["blinkit", 22 * M]],
      payment_method: [["prepaid", 118 * M], ["cod", 32 * M]],
    },
  },
  expected_rto_loss: {
    unit: "paise",
    definition: "expected return-to-origin loss (sample definition text)",
    value: 35 * M,
    groups: { payment_method: [["cod", 31 * M], ["prepaid", 4 * M]] },
  },
  ad_spend: {
    unit: "paise",
    definition: "sum of ad spend across platforms; keys are ad platforms (sample)",
    value: 126 * M,
    groups: { channel: [["meta", 84 * M], ["google_ads", 42 * M]] },
  },
  platform_reported_revenue: {
    unit: "paise",
    definition: "revenue as reported by ad platforms; not causal (sample)",
    value: 410 * M,
    groups: { channel: [["meta", 270 * M], ["google_ads", 140 * M]] },
  },
  mer: {
    unit: "ratio",
    definition: "marketing efficiency ratio (sample definition text)",
    value: 9.52,
    groups: {},
  },
  blended_cac: {
    unit: "paise",
    definition: "ad spend / new customers (sample definition text)",
    value: 61_000,
    groups: {},
  },
  platform_roas: {
    unit: "ratio",
    definition: "platform-reported revenue / ad spend. Platform-reported, not causal (sample)",
    value: 3.25,
    groups: { channel: [["meta", 3.21], ["google_ads", 3.33]] },
  },
};

const BASELINE_DAYS = 30;

function dayNumber(d: string): number {
  const [y, m, day] = d.split("-").map(Number);
  return Date.UTC(y, m - 1, day) / 86_400_000;
}

/** Deterministic +/-10% variation by period start so week-over-week differs. */
export function periodFactor(start: string): number {
  let h = 0;
  for (const ch of start) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return 1 + ((h % 21) - 10) / 100;
}

function scale(v: number, unit: MetricUnit, factor: number): number {
  if (unit === "ratio") return Math.round(v * (0.95 + (factor - 1) * 0.5) * 100) / 100; // ratios barely move with period length
  return Math.round(v * factor);
}

export function mockMetric(q: MetricQuery): MetricResult {
  const fx = FIXTURES[q.metric];
  const days = dayNumber(q.end) - dayNumber(q.start) + 1;
  const period = { start: q.start, end: q.end };
  const filters = q.filters ?? {};
  if (!fx) {
    return {
      metric: q.metric, value: null, unit: "count", currency: "INR", period, filters, group_by: q.groupBy ?? null,
      groups: [], definition: "unknown metric (sample)",
      data_quality: { status: "insufficient", issues: [`unknown_metric: ${q.metric}`], row_count: 0 },
    };
  }

  // Mirror the contract: short windows cannot support ratio metrics -> value null + insufficient.
  if (days < 7 && fx.unit !== "count") {
    return {
      metric: q.metric, value: null, unit: fx.unit, currency: "INR", period, filters, group_by: q.groupBy ?? null,
      groups: [], definition: fx.definition,
      data_quality: { status: "insufficient", issues: ["period_too_short: fewer than 7 days of data"], row_count: 0 },
    };
  }

  const isRatio = fx.unit === "ratio";
  const factor = (days / BASELINE_DAYS) * periodFactor(q.start);
  const f = isRatio || q.metric === "blended_cac" ? periodFactor(q.start) : factor;
  const rows = q.groupBy ? fx.groups[q.groupBy] ?? [] : [];
  const issues = [...(fx.issues?.["*"] ?? []), ...(q.groupBy ? fx.issues?.[q.groupBy] ?? [] : [])];

  return {
    metric: q.metric,
    value: scale(fx.value, fx.unit, f),
    unit: fx.unit,
    currency: "INR",
    period,
    filters,
    group_by: q.groupBy ?? null,
    groups: rows.map(([key, value]) => ({ key, value: scale(value, fx.unit, f) })),
    definition: fx.definition,
    data_quality: {
      status: issues.length ? "degraded" : "ok",
      issues,
      row_count: Math.round(2400 * (days / BASELINE_DAYS)),
    },
  };
}
