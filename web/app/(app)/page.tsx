"use client";

import { useMetrics, type MetricSpec } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { GroupBreakdown, KpiCard, TrendChart } from "@/components/metric-views";
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
  { metric: "net_revenue", groupBy: "day" },
  { metric: "contribution_margin", groupBy: "day" },
  { metric: "orders" },
  { metric: "aov" },
  { metric: "sessions" },
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
            <section aria-label="Order volume" className="grid gap-4 sm:grid-cols-3">
              <KpiCard title="Orders" result={r[15]} />
              <KpiCard title="Average order value" result={r[16]} />
              <KpiCard title="Sessions" result={r[17]} />
            </section>
            <TrendChart title="Daily trend" series={[{ name: "Net revenue", result: r[13] }, { name: "Contribution margin", result: r[14] }]} />
            <GroupBreakdown title="Net revenue by channel" result={r[12]} keyLabel="Channel" />
          </div>
        )}
      </Boundary>
    </>
  );
}
