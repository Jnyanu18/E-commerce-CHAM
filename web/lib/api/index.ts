import { createMockApi } from "@/lib/mocks";
import { getSupabase } from "@/lib/supabase";
import { ApiError } from "./errors";
import { createHttpClient } from "./http";
import { createSupabaseRepo } from "./supabase-repo";
import type { Api } from "./types";

export * from "./types";
export { ApiError } from "./errors";

/** Mocks are on unless explicitly disabled. Default true until the backend exists. */
export function isMockMode(value: string | undefined = process.env.NEXT_PUBLIC_USE_MOCKS): boolean {
  return value === undefined || value === "" ? true : value.toLowerCase() !== "false";
}

export function createLiveApi(): Api {
  const sb = getSupabase();
  if (!sb) {
    throw new ApiError("not_configured", "Live mode needs NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY");
  }
  const repo = createSupabaseRepo(sb);
  const http = createHttpClient({
    baseUrl: process.env.NEXT_PUBLIC_API_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    getToken: async () => (await sb.auth.getSession()).data.session?.access_token ?? null,
  });
  return { isMock: false, ...repo, ...http };
}

let cached: Api | undefined;
export function getApi(): Api {
  if (!cached) cached = isMockMode() ? createMockApi() : createLiveApi();
  return cached;
}
