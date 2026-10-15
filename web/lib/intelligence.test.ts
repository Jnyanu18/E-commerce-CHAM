import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { createHttpClient, type FetchLike } from "@/lib/api/http";
import { normalizeAnomalies, normalizeBrief, normalizeDataQuality, normalizeRootCause, unitForMetric } from "@/lib/api/normalize";
import { citationIds, parseInline, parseMarkdown } from "@/lib/markdown";
import { createMockApi, MOCK_ORG_FALLBACK, MOCK_ORG_ID, MOCK_ORG_INSUFFICIENT, MOCK_ORG_NO_BRIEF } from "@/lib/mocks";
import { mockMetric } from "@/lib/mocks/metrics";

describe("normalizeAnomalies", () => {
  it("maps engine records and tolerates missing fields / root_cause", () => {
    const out = normalizeAnomalies({
      anomalies: [
        { id: "a1", metric: "net_revenue", date: "2026-10-01", direction: "down", severity: "high", robust_z: -3.1, observed: 1, expected: 2, week_start: "2026-09-28" },
        { metric: "mer" },
      ],
    });
    expect(out).toHaveLength(2);
    expect(out[0].root_cause).toBeNull();
    expect(out[1]).toMatchObject({ id: "mer:1", severity: null, direction: null, observed: null });
  });
  it("renders root-cause drivers defensively without inventing shares", () => {
    const rc = normalizeRootCause({
      method: "LMDI", drivers: [{ driver: "rto_rate", change: "+4pp", "estimated contribution": -100, confidence: { level: "low", reason: "x" } }, "junk"],
    });
    expect(rc?.drivers).toHaveLength(1);
    expect(rc?.drivers[0].title).toBe("rto_rate");
    expect(rc?.drivers[0].fields.map((f) => f.label)).toContain("estimated contribution");
    expect(rc?.drivers[0].fields.find((f) => f.label === "confidence")?.value).toContain("low");
    expect(normalizeRootCause(null)).toBeNull();
    expect(normalizeRootCause({})).toBeNull();
  });
  it("drops duplicate ids, rejects bad bodies and ignores unknown severities", () => {
    expect(normalizeAnomalies({ anomalies: [{ id: "x", metric: "m" }, { id: "x", metric: "m" }] })).toHaveLength(1);
    expect(normalizeAnomalies({ anomalies: [{ id: "x", metric: "m", severity: "low" }] })[0].severity).toBeNull();
    expect(normalizeAnomalies({ anomalies: [] })).toEqual([]);
    expect(() => normalizeAnomalies([])).toThrow(ApiError);
    expect(() => normalizeAnomalies(null)).toThrow(ApiError);
  });
});

describe("normalizeBrief", () => {
  it("handles status none", () => {
    expect(normalizeBrief({ status: "none", reason: "r" })).toEqual({ status: "none", reason: "r" });
  });
  it("normalises ok briefs and drops malformed calls", () => {
    const b = normalizeBrief({
      status: "ok", week_start: "2026-10-05", generated_at: "t", emailed_at: null, mode: "fallback", markdown: "# x [c1]",
      calls: [{ call_id: "c1", tool: "get_metric", args: { metric: "mer" }, ok: true, result: { value: 1 } }, { tool: "no id" }, 5],
    });
    expect(b.status === "ok" && b.calls).toHaveLength(1);
    expect(b.status === "ok" && b.mode).toBe("fallback");
  });
  it("rejects invalid shapes", () => {
    expect(() => normalizeBrief({ status: "ok" })).toThrow(ApiError);
    expect(() => normalizeBrief("x")).toThrow(ApiError);
  });
});

describe("normalizeDataQuality", () => {
  it("maps checks and defaults unknown severities to warn", () => {
    const r = normalizeDataQuality({ org_id: "o", generated_at: "t", status: "warn", checks: [{ code: "c", severity: "weird", count: 2, message: "m", examples: ["a"] }, { code: "d" }] });
    expect(r.checks[0].severity).toBe("warn");
    expect(r.checks[1]).toMatchObject({ count: 0, examples: [] });
    expect(() => normalizeDataQuality({})).toThrow(ApiError);
  });
});

describe("http client intelligence endpoints", () => {
  it("calls the engine paths and normalises", async () => {
    const urls: string[] = [];
    const fetchImpl: FetchLike = async (u) => {
      urls.push(u);
      const body = u.includes("anomalies") ? { anomalies: [] } : u.includes("briefs") ? { status: "none", reason: "r" } : { org_id: "o", status: "ok", checks: [] };
      return { ok: true, status: 200, json: async () => body };
    };
    const c = createHttpClient({ baseUrl: "http://api/", fetchImpl });
    expect(await c.getAnomalies("o 1")).toEqual([]);
    expect((await c.getBrief("o")).status).toBe("none");
    expect((await c.getDataQuality("o")).status).toBe("ok");
    expect(urls[0]).toBe("http://api/anomalies?org_id=o%201");
    expect(urls[1]).toBe("http://api/briefs/latest?org_id=o");
    expect(urls[2]).toBe("http://api/data-quality?org_id=o");
  });
});

describe("markdown parser", () => {
  it("parses headings, lists, bold, code and citations", () => {
    const b = parseMarkdown("# T\n\nHello **bold** `x` [c1][c2].\n\n- a\n- b [c3]\n\n1. one\n2. two");
    expect(b.map((x) => x.t)).toEqual(["heading", "paragraph", "ul", "ol"]);
    const para = b[1];
    expect(para.t === "paragraph" && para.inline.filter((i) => i.t === "cite").map((i) => (i as { id: string }).id)).toEqual(["c1", "c2"]);
  });
  it("never produces html: markup stays literal text", () => {
    const b = parseMarkdown("<script>alert(1)</script> <img src=x onerror=1> [link](javascript:alert(1))");
    expect(b).toHaveLength(1);
    const kinds = b[0].t === "paragraph" ? b[0].inline.map((i) => i.t) : [];
    expect(kinds.every((k) => k === "text")).toBe(true);
  });
  it("handles empty input, CRLF and unknown bracket text", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown("a\r\nb")).toHaveLength(1);
    expect(parseInline("[not a cite] and [c12]")).toEqual([{ t: "text", v: "[not a cite] and " }, { t: "cite", id: "c12" }]);
  });
  it("lists unique citation ids", () => {
    expect(citationIds("[c1] x [c2] [c1]")).toEqual(["c1", "c2"]);
  });
});

describe("intelligence mocks", () => {
  it("serves scenarios by org id", async () => {
    const api = createMockApi();
    const ok = await api.getBrief(MOCK_ORG_ID);
    expect(ok.status === "ok" && ok.mode).toBe("narrative");
    const fb = await api.getBrief(MOCK_ORG_FALLBACK);
    expect(fb.status === "ok" && fb.mode).toBe("fallback");
    expect((await api.getBrief(MOCK_ORG_NO_BRIEF)).status).toBe("none");
    const ins = await api.getBrief(MOCK_ORG_INSUFFICIENT);
    expect(ins.status === "ok" && ins.markdown).toMatch(/Insufficient data/);
  });
  it("every citation in mock briefs resolves to a stored call", async () => {
    const api = createMockApi();
    for (const org of [MOCK_ORG_ID, MOCK_ORG_FALLBACK, MOCK_ORG_INSUFFICIENT]) {
      const b = await api.getBrief(org);
      if (b.status !== "ok") throw new Error("expected ok");
      const ids = new Set(b.calls.map((c) => c.call_id));
      for (const id of citationIds(b.markdown)) expect(ids.has(id)).toBe(true);
    }
  });
  it("anomalies include one without root cause and data quality has non-ok checks", async () => {
    const api = createMockApi();
    expect((await api.getAnomalies(MOCK_ORG_ID)).some((a) => a.root_cause === null)).toBe(true);
    expect((await api.getDataQuality(MOCK_ORG_ID)).checks.some((c) => c.severity !== "ok")).toBe(true);
  });
});

describe("day grain and new metrics (mock)", () => {
  const q = { orgId: "o", metric: "net_revenue" as const, start: "2026-09-16", end: "2026-10-15", groupBy: "day" as const };
  it("returns one integer-paise group per day with unique ISO keys", () => {
    const r = mockMetric(q);
    expect(r.groups).toHaveLength(30);
    expect(r.groups[0].key).toBe("2026-09-16");
    expect(r.groups[29].key).toBe("2026-10-15");
    expect(r.groups.every((g) => Number.isInteger(g.value))).toBe(true);
    expect(new Set(r.groups.map((g) => g.key)).size).toBe(30);
  });
  it("sessions is insufficient, orders/aov are valid", () => {
    const s = mockMetric({ ...q, metric: "sessions", groupBy: undefined });
    expect(s.value).toBeNull();
    expect(s.data_quality.status).toBe("insufficient");
    expect(mockMetric({ ...q, metric: "orders", groupBy: undefined }).unit).toBe("count");
    expect(mockMetric({ ...q, metric: "aov", groupBy: undefined }).unit).toBe("paise");
  });
  it("maps metric names to display units", () => {
    expect(unitForMetric("net_revenue")).toBe("paise");
    expect(unitForMetric("mer")).toBe("ratio");
    expect(unitForMetric("orders")).toBe("count");
  });
});
