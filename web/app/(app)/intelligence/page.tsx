"use client";

import Link from "next/link";
import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { Boundary, EmptyState } from "@/components/states";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { Anomaly, Severity } from "@/lib/api";
import { formatByUnit, formatIST, formatPercent } from "@/lib/format";
import { labelForKey } from "@/components/metric-views";

const SEVERITY_TONE: Record<Severity, "danger" | "warning" | "info" | "neutral"> = {
  critical: "danger",
  high: "warning",
  medium: "info",
  low: "neutral",
};

function AnomalyCard({ a }: { a: Anomaly }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Badge tone={SEVERITY_TONE[a.severity]}>{a.severity.toUpperCase()}</Badge>
          <span>{a.metric.replace(/_/g, " ")}</span>
          <span className="ml-auto text-xs font-normal text-muted-foreground">{formatIST(a.detected_at)}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{a.summary}</p>
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-muted-foreground">
          <div><dt className="inline">Observed: </dt><dd className="inline font-medium text-foreground">{formatByUnit(a.observed, a.unit)}</dd></div>
          <div><dt className="inline">Expected: </dt><dd className="inline font-medium text-foreground">{formatByUnit(a.expected, a.unit)}</dd></div>
        </dl>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Drivers</h3>
          {a.drivers.length === 0 ? (
            <p className="text-muted-foreground">No drivers identified.</p>
          ) : (
            <ul className="list-disc space-y-0.5 pl-5">
              {a.drivers.map((d) => (
                <li key={`${d.dimension}:${d.key}`}>
                  <span className="font-medium">{labelForKey(d.dimension)}: {labelForKey(d.key)}</span> - {d.detail}
                  {d.share !== null && <span className="text-muted-foreground"> ({formatPercent(d.share * 100, { digits: 0 })} of change)</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Evidence</h3>
          {a.evidence.length === 0 ? (
            <p className="text-muted-foreground">No evidence attached.</p>
          ) : (
            <ul className="space-y-0.5">
              {a.evidence.map((e) => (
                <li key={e.call_id}>
                  <code className="rounded bg-muted px-1 text-xs">[{e.call_id}]</code> {e.description}
                </li>
              ))}
            </ul>
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
        <PageHeader title="Anomalies" description="Detected deviations with drivers and the tool calls that back them." />
        <Link href="/intelligence/brief" className="text-sm font-medium underline">Open weekly AI brief</Link>
      </div>
      <Boundary state={state} onRetry={state.reload} rows={5}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No anomalies detected" hint="Nothing has deviated from expectation in this window." />
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
