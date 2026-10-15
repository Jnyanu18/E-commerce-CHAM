"use client";

import Link from "next/link";
import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { Boundary, EmptyState } from "@/components/states";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { Anomaly, Severity } from "@/lib/api";
import { unitForMetric } from "@/lib/api/normalize";
import { formatByUnit, formatCalendarDate } from "@/lib/format";

const SEVERITY_TONE: Record<Severity, "danger" | "warning" | "info"> = {
  critical: "danger",
  high: "warning",
  medium: "info",
};

function AnomalyCard({ a }: { a: Anomaly }) {
  const unit = unitForMetric(a.metric);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {a.severity && <Badge tone={SEVERITY_TONE[a.severity]}>{a.severity.toUpperCase()}</Badge>}
          <span>{a.metric.replace(/_/g, " ")}</span>
          {a.direction && <span aria-label={`direction ${a.direction}`}>{a.direction === "up" ? "▲ up" : "▼ down"}</span>}
          <span className="ml-auto text-xs font-normal text-muted-foreground">{formatCalendarDate(a.date)} (IST)</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-muted-foreground">
          <div><dt className="inline">Observed: </dt><dd className="inline font-medium text-foreground">{formatByUnit(a.observed, unit)}</dd></div>
          <div><dt className="inline">Expected: </dt><dd className="inline font-medium text-foreground">{formatByUnit(a.expected, unit)}</dd></div>
          {a.robust_z !== null && <div><dt className="inline">Robust z: </dt><dd className="inline font-medium text-foreground">{a.robust_z.toFixed(1)}</dd></div>}
        </dl>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Root cause</h3>
          {a.root_cause === null ? (
            <p className="text-muted-foreground">No root cause computed.</p>
          ) : (
            <div className="space-y-2">
              {a.root_cause.notes.length > 0 && (
                <p className="text-muted-foreground">{a.root_cause.notes.map((n) => `${n.label}: ${n.value}`).join(" | ")}</p>
              )}
              {a.root_cause.drivers.length === 0 ? (
                <p className="text-muted-foreground">No drivers reported.</p>
              ) : (
                <ul className="space-y-2">
                  {a.root_cause.drivers.map((d) => (
                    <li key={d.title} className="rounded border border-border p-2">
                      <p className="font-medium">{d.title.replace(/_/g, " ")}</p>
                      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                        {d.fields.map((f) => (
                          <div key={f.label} className="contents">
                            <dt className="text-muted-foreground">{f.label}</dt>
                            <dd className="break-words">{f.value}</dd>
                          </div>
                        ))}
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function AnomaliesPage() {
  const { api, orgId } = useApp();
  const state = useAsync(orgId ? () => api.getAnomalies(orgId) : null, `anoms|${orgId}`);
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <PageHeader title="Anomalies" description="Deviations flagged by the intelligence job, with root-cause drivers when computed." />
        <Link href="/intelligence/brief" className="text-sm font-medium underline">Open weekly AI brief</Link>
      </div>
      <Boundary state={state} onRetry={state.reload} rows={5}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No anomalies detected" hint="Nothing has deviated from expectation in the recent weeks." />
          ) : (
            <ul className="space-y-4" aria-label="Anomaly feed">
              {items.map((a) => (
                <li key={a.id}><AnomalyCard a={a} /></li>
              ))}
            </ul>
          )
        }
      </Boundary>
    </>
  );
}
