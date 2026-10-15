import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { buildMetricUrl, createHttpClient, type FetchLike } from "@/lib/api/http";
import { isMockMode } from "@/lib/api";
import { parseMetricResult } from "@/lib/api/validate";
import { createMockApi, MOCK_ORG_ID } from "@/lib/mocks";
import { mockMetric, periodFactor } from "@/lib/mocks/metrics";

const valid = {
  metric: "contribution_margin",
  value: 720000,
  unit: "paise",
  currency: "INR",
  period: { start: "2026-09-01", end: "2026-09-30" },
  filters: { channel: ["shopify"] },
  group_by: "channel",
  groups: [{ key: "shopify", value: 500000 }],
  definition: "d",
  data_quality: { status: "ok", issues: [], row_count: 10 },
};

describe("parseMetricResult", () => {
  it("accepts a contract-shaped payload", () => {
    const r = parseMetricResult(valid);
    expect(r.value).toBe(720000);
    expect(r.groups).toHaveLength(1);
  });
  it("accepts null value with insufficient status (empty data)", () => {
    const r = parseMetricResult({ ...valid, value: null, groups: [], data_quality: { status: "insufficient", issues: ["x"], row_count: 0 } });
    expect(r.value).toBeNull();
    expect(r.groups).toEqual([]);
  });
  it("rejects invalid payloads", () => {
    expect(() => parseMetricResult(null)).toThrow(ApiError);
    expect(() => parseMetricResult({ ...valid, unit: "dollars" })).toThrow(/unit/);
    expect(() => parseMetricResult({ ...valid, value: "5" })).toThrow(/value/);
    expect(() => parseMetricResult({ ...valid, value: 10.5 })).toThrow(/integer/);
    expect(() => parseMetricResult({ ...valid, data_quality: { status: "great", issues: [], row_count: 1 } })).toThrow(/status/);
    expect(() => parseMetricResult({ ...valid, value_paise: 1, value: undefined })).toThrow(ApiError);
  });
  it("rejects duplicate group keys", () => {
    expect(() => parseMetricResult({ ...valid, groups: [{ key: "a", value: 1 }, { key: "a", value: 2 }] })).toThrow(/duplicate/);
  });
  it("allows ratio decimals", () => {
    expect(parseMetricResult({ ...valid, unit: "ratio", value: 2.5 }).value).toBe(2.5);
  });
});

describe("http client", () => {
  const q = { orgId: "o1", metric: "net_revenue" as const, start: "2026-09-01", end: "2026-09-30" };

  it("builds the contract URL", () => {
    const url = buildMetricUrl("http://api/", { ...q, groupBy: "channel", filters: { channel: ["shopify"] } });
    expect(url).toBe(
      "http://api/metrics/net_revenue?org_id=o1&start=2026-09-01&end=2026-09-30&group_by=channel&filters=%7B%22channel%22%3A%5B%22shopify%22%5D%7D",
    );
    expect(buildMetricUrl("http://api", q)).not.toContain("group_by");
  });

  it("sends bearer token and validates the response", async () => {
    let seen: Record<string, string> | undefined;
    const fetchImpl: FetchLike = async (_u, init) => {
      seen = init?.headers;
      return { ok: true, status: 200, json: async () => valid };
    };
    const c = createHttpClient({ baseUrl: "http://api", getToken: async () => "jwt", fetchImpl });
    expect((await c.getMetric(q)).metric).toBe("contribution_margin");
    expect(seen?.Authorization).toBe("Bearer jwt");
  });

  it("maps errors to ApiError codes", async () => {
    const mk = (res: Awaited<ReturnType<FetchLike>> | Error) =>
      createHttpClient({ baseUrl: "http://api", fetchImpl: async () => { if (res instanceof Error) throw res; return res; } });
    await expect(mk({ ok: false, status: 401, json: async () => ({}) }).getMetric(q)).rejects.toMatchObject({ code: "unauthorized" });
    await expect(mk({ ok: false, status: 500, json: async () => ({}) }).getMetric(q)).rejects.toMatchObject({ code: "http", status: 500 });
    await expect(mk(new Error("down")).getMetric(q)).rejects.toMatchObject({ code: "network" });
    await expect(mk({ ok: true, status: 200, json: async () => ({ nope: 1 }) }).getMetric(q)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(mk({ ok: true, status: 200, json: async () => { throw new Error("bad json"); } }).getMetric(q)).rejects.toMatchObject({ code: "invalid_response" });
  });
});

describe("mock layer", () => {
  const q = { orgId: MOCK_ORG_ID, metric: "contribution_margin" as const, start: "2026-09-15", end: "2026-10-14", groupBy: "channel" as const };

  it("returns contract-valid MetricResults for every group_by", () => {
    for (const metric of ["net_revenue", "contribution_margin", "contribution_after_marketing", "mer", "blended_cac", "platform_roas", "ad_spend", "platform_reported_revenue", "expected_rto_loss"] as const) {
      for (const groupBy of [undefined, "channel", "sku", "category", "city", "city_tier", "payment_method"] as const) {
        const r = mockMetric({ ...q, metric, groupBy });
        expect(() => parseMetricResult(JSON.parse(JSON.stringify(r)))).not.toThrow();
      }
    }
  });
  it("money values are integer paise and group keys are unique", () => {
    const r = mockMetric(q);
    expect(Number.isInteger(r.value)).toBe(true);
    expect(new Set(r.groups.map((g) => g.key)).size).toBe(r.groups.length);
  });
  it("tier keys use the canonical enum", () => {
    const keys = mockMetric({ ...q, groupBy: "city_tier" }).groups.map((g) => g.key);
    expect(keys).toEqual(["metro", "tier_1", "tier_2", "tier_3", "unknown"]);
  });
  it("is deterministic and differs between periods", () => {
    expect(mockMetric(q)).toEqual(mockMetric(q));
    const prev = mockMetric({ ...q, start: "2026-08-15", end: "2026-09-14" });
    expect(prev.value).not.toBe(mockMetric(q).value);
    expect(periodFactor("2026-09-15")).toBeGreaterThanOrEqual(0.9);
  });
  it("reports degraded data quality and insufficient short windows", () => {
    expect(mockMetric({ ...q, groupBy: "sku" }).data_quality.status).toBe("degraded");
    const short = mockMetric({ ...q, metric: "mer", start: "2026-10-12", end: "2026-10-14" });
    expect(short.value).toBeNull();
    expect(short.data_quality.status).toBe("insufficient");
  });
  it("unknown metric yields insufficient, not zero", () => {
    const r = mockMetric({ ...q, metric: "nope" as never });
    expect(r.value).toBeNull();
  });
  it("cogs upsert is idempotent per (sku, effective_from) and validates", async () => {
    const api = createMockApi();
    const before = (await api.listSkuCogs(MOCK_ORG_ID)).length;
    await api.upsertSkuCogs({ org_id: MOCK_ORG_ID, sku: "SKU-NEW", effective_from: "2026-10-01", cogs_paise: 5000 });
    await api.upsertSkuCogs({ org_id: MOCK_ORG_ID, sku: "SKU-NEW", effective_from: "2026-10-01", cogs_paise: 6000 });
    const rows = await api.listSkuCogs(MOCK_ORG_ID);
    expect(rows).toHaveLength(before + 1);
    expect(rows.find((r) => r.sku === "SKU-NEW")?.cogs_paise).toBe(6000);
    await expect(api.upsertSkuCogs({ org_id: MOCK_ORG_ID, sku: " ", effective_from: "2026-10-01", cogs_paise: 1 })).rejects.toMatchObject({ code: "validation" });
    await expect(api.upsertSkuCogs({ org_id: MOCK_ORG_ID, sku: "X", effective_from: "2026-10-01", cogs_paise: -1 })).rejects.toMatchObject({ code: "validation" });
    await expect(api.upsertSkuCogs({ org_id: MOCK_ORG_ID, sku: "X", effective_from: "2026-10-01", cogs_paise: 1.5 })).rejects.toMatchObject({ code: "validation" });
  });
  it("integrations expose only safe columns", async () => {
    const rows = await createMockApi().listIntegrations(MOCK_ORG_ID);
    for (const r of rows) expect(Object.keys(r).sort()).toEqual(["account_ref", "backfill_done", "id", "last_error", "last_synced_at", "org_id", "provider", "status"]);
  });
});

describe("env flag", () => {
  it("defaults to mocks and honours explicit false", () => {
    expect(isMockMode(undefined)).toBe(true);
    expect(isMockMode("")).toBe(true);
    expect(isMockMode("true")).toBe(true);
    expect(isMockMode("false")).toBe(false);
    expect(isMockMode("FALSE")).toBe(false);
  });
});

describe("validator edge cases for live engine output", () => {
  const base = {
    metric: "mer", value: null, unit: "ratio", currency: "INR",
    period: { start: "2026-09-01", end: "2026-09-30" }, filters: {}, group_by: null, groups: [],
    definition: "d", data_quality: { status: "insufficient", issues: ["no spend"], row_count: 0 },
  };
  it("accepts null value, ratio unit, insufficient status", () => {
    const r = parseMetricResult(base);
    expect(r.value).toBeNull();
    expect(r.unit).toBe("ratio");
    expect(r.data_quality.status).toBe("insufficient");
  });
  it("accepts null group values", () => {
    expect(parseMetricResult({ ...base, groups: [{ key: "google", value: null }] }).groups[0].value).toBeNull();
  });
});
