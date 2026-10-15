"use client";

import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { Boundary } from "@/components/states";
import { Badge, Card, CardContent } from "@/components/ui/primitives";
import type { CheckSeverity } from "@/lib/api";
import { formatIST } from "@/lib/format";

const RANK: Record<CheckSeverity, number> = { error: 3, warn: 2, info: 1, ok: 0 };
const TONE: Record<CheckSeverity, "danger" | "warning" | "info" | "success"> = { error: "danger", warn: "warning", info: "info", ok: "success" };

function example(e: unknown): string {
  return typeof e === "string" ? e : JSON.stringify(e);
}

export default function DataQualityPage() {
  const { api, orgId } = useApp();
  const state = useAsync(orgId ? () => api.getDataQuality(orgId) : null, `dq|${orgId}`);
  return (
    <>
      <PageHeader title="Data quality" description="Problems found in the underlying data. Nothing here is hidden or auto-repaired." />
      <Boundary state={state} onRetry={state.reload} rows={4}>
        {(r) => {
          const problems = r.checks.filter((c) => c.severity !== "ok").sort((a, b) => RANK[b.severity] - RANK[a.severity] || b.count - a.count);
          const passed = r.checks.length - problems.length;
          return (
            <div className="space-y-4">
              <p className="flex flex-wrap items-center gap-2 text-sm">
                Overall: <Badge tone={TONE[r.status]}>{r.status}</Badge>
                <span className="text-muted-foreground">Checked {formatIST(r.generated_at)}. {passed} of {r.checks.length} checks passed.</span>
              </p>
              {problems.length === 0 ? (
                <div role="status" className="rounded-md border border-border p-4 text-sm">All data-quality checks passed.</div>
              ) : (
                <ul className="space-y-3" aria-label="Data quality issues">
                  {problems.map((c) => (
                    <li key={c.code}>
                      <Card>
                        <CardContent className="space-y-1 pt-4 text-sm">
                          <p className="flex flex-wrap items-center gap-2">
                            <Badge tone={TONE[c.severity]}>{c.severity}</Badge>
                            <span className="font-medium">{c.code.replace(/_/g, " ")}</span>
                            <span className="text-muted-foreground">count: {c.count}</span>
                          </p>
                          <p>{c.message}</p>
                          {c.examples.length > 0 && (
                            <p className="text-muted-foreground">
                              Examples: {c.examples.map((e) => <code key={example(e)} className="mr-1 rounded bg-muted px-1 text-xs">{example(e)}</code>)}
                            </p>
                          )}
                        </CardContent>
                      </Card>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        }}
      </Boundary>
    </>
  );
}
