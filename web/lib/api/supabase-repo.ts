import type { SupabaseClient } from "@supabase/supabase-js";
import { ApiError } from "./errors";
import { INTEGRATION_SAFE_COLUMNS, type Integration, type SkuCogs } from "./types";

/** Direct Supabase access allowed by RLS: org lookup, safe integration columns, sku_cogs read/write. */
export function createSupabaseRepo(sb: SupabaseClient) {
  return {
    async getOrgId(): Promise<string | null> {
      const { data, error } = await sb.from("org_members").select("org_id").limit(1);
      if (error) throw new ApiError("http", error.message);
      return data?.[0]?.org_id ?? null;
    },
    async listIntegrations(orgId: string): Promise<Integration[]> {
      // Never select("*"): credentials_enc is not readable by browser roles.
      const { data, error } = await sb.from("integrations").select(INTEGRATION_SAFE_COLUMNS).eq("org_id", orgId).order("provider");
      if (error) throw new ApiError("http", error.message);
      return (data ?? []) as unknown as Integration[];
    },
    async listSkuCogs(orgId: string): Promise<SkuCogs[]> {
      const { data, error } = await sb
        .from("sku_cogs")
        .select("id,org_id,sku,effective_from,cogs_paise")
        .eq("org_id", orgId)
        .order("sku")
        .order("effective_from", { ascending: false });
      if (error) throw new ApiError("http", error.message);
      return (data ?? []) as SkuCogs[];
    },
    async upsertSkuCogs(row: SkuCogs): Promise<SkuCogs> {
      if (!row.sku.trim()) throw new ApiError("validation", "SKU is required");
      if (!Number.isSafeInteger(row.cogs_paise) || row.cogs_paise < 0) throw new ApiError("validation", "COGS must be a non-negative integer of paise");
      const { data, error } = await sb
        .from("sku_cogs")
        .upsert(
          { org_id: row.org_id, sku: row.sku.trim(), effective_from: row.effective_from, cogs_paise: row.cogs_paise },
          { onConflict: "org_id,sku,effective_from" },
        )
        .select("id,org_id,sku,effective_from,cogs_paise")
        .single();
      if (error) throw new ApiError("http", error.message);
      return data as SkuCogs;
    },
  };
}
