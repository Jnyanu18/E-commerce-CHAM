// Live-mode smoke test (not run in CI). Does NOT start the engine.
// Usage: NEXT_PUBLIC_API_URL=http://localhost:8000 SMOKE_TOKEN=<supabase jwt> SMOKE_ORG_ID=<uuid> node scripts/live-smoke.mjs
const base = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
const token = process.env.SMOKE_TOKEN;
const orgId = process.env.SMOKE_ORG_ID;
if (!token || !orgId) {
  console.error("Set SMOKE_TOKEN (dev Supabase access token) and SMOKE_ORG_ID.");
  process.exit(2);
}
const end = new Date().toISOString().slice(0, 10);
const start = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
const metrics = ["net_revenue", "contribution_margin", "contribution_after_marketing", "mer", "blended_cac", "platform_roas", "expected_rto_loss"];
const UNITS = ["paise", "ratio", "count"];
const STATUSES = ["ok", "degraded", "insufficient"];

function check(r) {
  const errs = [];
  if (typeof r.metric !== "string") errs.push("metric");
  if (!UNITS.includes(r.unit)) errs.push(`unit=${r.unit}`);
  if (r.value !== null && !Number.isFinite(r.value)) errs.push("value");
  if (r.unit === "paise" && r.value !== null && !Number.isInteger(r.value)) errs.push("paise not integer");
  if (!r.period?.start || !r.period?.end) errs.push("period");
  if (!STATUSES.includes(r.data_quality?.status)) errs.push("data_quality.status");
  if (!Array.isArray(r.data_quality?.issues)) errs.push("data_quality.issues");
  if (r.value === null && r.data_quality?.status !== "insufficient") errs.push("null value without insufficient status");
  return errs;
}

let failed = 0;
for (const m of metrics) {
  const url = `${base}/metrics/${m}?${new URLSearchParams({ org_id: orgId, start, end })}`;
  try {
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}`, Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${await res.text()}`);
    const errs = check(await res.json());
    if (errs.length) throw new Error(`contract: ${errs.join(", ")}`);
    console.log(`PASS ${m}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${m}: ${e.message}`);
  }
}
process.exit(failed ? 1 : 0);
