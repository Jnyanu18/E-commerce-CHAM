import { ApiError } from "./errors";
import { parseMetricResult } from "./validate";
import { normalizeAnomalies, normalizeBrief, normalizeDataQuality } from "./normalize";
import type { Anomaly, Brief, DataQualityReport, MetricQuery, MetricResult } from "./types";

export type FetchLike = (input: string, init?: { headers?: Record<string, string> }) => Promise<{
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}>;

export interface HttpClientOptions {
  baseUrl: string;
  /** Supplies the Supabase JWT; the API checks org membership server-side. */
  getToken?: () => Promise<string | null>;
  fetchImpl?: FetchLike;
}

/** Builds the query string for GET /metrics/{metric}?org_id&start&end&group_by&filters. */
export function buildMetricUrl(baseUrl: string, q: MetricQuery): string {
  const params = new URLSearchParams({ org_id: q.orgId, start: q.start, end: q.end });
  if (q.groupBy) params.set("group_by", q.groupBy);
  if (q.filters && Object.keys(q.filters).length > 0) params.set("filters", JSON.stringify(q.filters));
  return `${baseUrl.replace(/\/+$/, "")}/metrics/${encodeURIComponent(q.metric)}?${params.toString()}`;
}

export function createHttpClient(opts: HttpClientOptions) {
  const root = opts.baseUrl.replace(/\/+$/, "");
  const doFetch: FetchLike = opts.fetchImpl ?? ((url, init) => fetch(url, init));

  async function get(url: string): Promise<unknown> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = opts.getToken ? await opts.getToken() : null;
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await doFetch(url, { headers });
    } catch (e) {
      throw new ApiError("network", e instanceof Error ? e.message : "Network error");
    }
    if (res.status === 401 || res.status === 403) throw new ApiError("unauthorized", "Not authorised for this organisation", res.status);
    if (!res.ok) throw new ApiError("http", `API responded ${res.status}`, res.status);
    try {
      return await res.json();
    } catch {
      throw new ApiError("invalid_response", "Response was not valid JSON", res.status);
    }
  }

  return {
    async getMetric(q: MetricQuery): Promise<MetricResult> {
      return parseMetricResult(await get(buildMetricUrl(opts.baseUrl, q)));
    },
    async getAnomalies(orgId: string): Promise<Anomaly[]> {
      return normalizeAnomalies(await get(`${root}/anomalies?org_id=${encodeURIComponent(orgId)}`));
    },
    async getBrief(orgId: string): Promise<Brief> {
      return normalizeBrief(await get(`${root}/briefs/latest?org_id=${encodeURIComponent(orgId)}`));
    },
    async getDataQuality(orgId: string): Promise<DataQualityReport> {
      return normalizeDataQuality(await get(`${root}/data-quality?org_id=${encodeURIComponent(orgId)}`));
    },
  };
}
