"use client";

import { useMetrics, type MetricSpec } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { GroupBreakdown, KpiCard } from "@/components/metric-views";
import { Boundary, DataQualityNotice } from "@/components/states";
import { percentChange } from "@/lib/presentation";

const KPI_METRICS = [
  { metric: "net_revenue", title: "Net revenue" },
  { metric: "contribution_after_marketing", title: "Contribution after marketing" },
  { metric: "mer", title: "MER" },
  { metric: "blended_cac", title: "Blended CAC" },
] as const;

// Order matters: [range x4][week x4][prevWeek x4][net_revenue by channel]
const SPECS: MetricSpec[] = [
  ...KPI_METRICS.map((k) => ({ metric: k.metric })),
  ...KPI_METRICS.map((k) => ({ metric: k.metric, window: "week" as const })),
  ...KPI_METRICS.map((k) => ({ metric: k.metric, window: "prevWeek" as const })),
  { metric: "net_revenue", groupBy: "channel" },
];

export default function OverviewPage() {
  const state = useMetrics(SPECS);
  return (
    <>
      <PageHeader title="Overview" description="Headline metrics for the selected period. Week-over-week compares the last 7 days with the 7 before." />
      <Boundary state={state} onRetry={state.reload} rows={4}>
        {(r) => (
          <div className="space-y-6">
            <DataQualityNotice results={r} />
            <section aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {KPI_METRICS.map((k, i) => (
                <KpiCard
                  key={k.metric}
                  title={k.title}
                  result={r[i]}
                  deltaPct={percentChange(r[4 + i].value, r[8 + i].value)}
                  deltaLabel="WoW"
                />
              ))}
            </section>
            <GroupBreakdown title="Net revenue by channel" result={r[12]} keyLabel="Channel" />
          </div>
        )}
      </Boundary>
    </>
  );
}
