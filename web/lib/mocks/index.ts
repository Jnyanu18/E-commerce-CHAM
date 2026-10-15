import { ApiError } from "@/lib/api/errors";
import type { Api, Integration, SkuCogs } from "@/lib/api/types";
import { MOCK_ANOMALIES, MOCK_BRIEF, MOCK_BRIEF_INSUFFICIENT, MOCK_ORG_ID, MOCK_ORG_INSUFFICIENT } from "./intelligence";
import { mockMetric } from "./metrics";

export { MOCK_ORG_ID, MOCK_ORG_INSUFFICIENT };

const MOCK_INTEGRATIONS: Integration[] = [
  { id: "int-1", org_id: MOCK_ORG_ID, provider: "shopify", account_ref: "sample-store.myshopify.com", status: "active", last_synced_at: "2026-10-14T01:00:00Z", last_error: null, backfill_done: true },
  { id: "int-2", org_id: MOCK_ORG_ID, provider: "meta", account_ref: "act_0000000000", status: "active", last_synced_at: "2026-10-14T01:05:00Z", last_error: null, backfill_done: true },
  { id: "int-3", org_id: MOCK_ORG_ID, provider: "google_ads", account_ref: "000-000-0000", status: "error", last_synced_at: "2026-10-11T01:00:00Z", last_error: "Sample error: token refresh failed", backfill_done: false },
  { id: "int-4", org_id: MOCK_ORG_ID, provider: "shiprocket", account_ref: "sample-account", status: "pending", last_synced_at: null, last_error: null, backfill_done: false },
];

const DEFAULT_COGS: SkuCogs[] = [
  { id: "c1", org_id: MOCK_ORG_ID, sku: "SKU-SERUM-30", effective_from: "2026-04-01", cogs_paise: 21_000 },
  { id: "c2", org_id: MOCK_ORG_ID, sku: "SKU-SERUM-30", effective_from: "2026-09-01", cogs_paise: 23_500 },
  { id: "c3", org_id: MOCK_ORG_ID, sku: "SKU-CREAM-50", effective_from: "2026-04-01", cogs_paise: 18_000 },
];

/** In-memory mock backend. Mutations live only for the page session and never reach any server. */
export function createMockApi(): Api {
  let cogs = DEFAULT_COGS.map((r) => ({ ...r }));
  return {
    isMock: true,
    async getOrgId() {
      return MOCK_ORG_ID;
    },
    async getMetric(q) {
      return mockMetric(q);
    },
    async getAnomalies() {
      return MOCK_ANOMALIES;
    },
    async getBrief(orgId) {
      return orgId === MOCK_ORG_INSUFFICIENT ? MOCK_BRIEF_INSUFFICIENT : MOCK_BRIEF;
    },
    async listIntegrations() {
      return MOCK_INTEGRATIONS;
    },
    async listSkuCogs() {
      return cogs.map((r) => ({ ...r }));
    },
    async upsertSkuCogs(row) {
      if (!row.sku.trim()) throw new ApiError("validation", "SKU is required");
      if (!Number.isInteger(row.cogs_paise) || row.cogs_paise < 0) throw new ApiError("validation", "COGS must be a non-negative integer of paise");
      const i = cogs.findIndex((r) => r.sku === row.sku && r.effective_from === row.effective_from);
      const saved = { ...row, id: i >= 0 ? cogs[i].id : `c${cogs.length + 1}` };
      if (i >= 0) cogs[i] = saved;
      else cogs = [...cogs, saved];
      return { ...saved };
    },
  };
}
