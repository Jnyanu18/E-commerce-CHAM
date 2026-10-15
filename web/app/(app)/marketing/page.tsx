"use client";

import { useMetrics, type MetricSpec } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { GroupBreakdown, KpiCard, labelForKey } from "@/components/metric-views";
import { Boundary, DataQualityNotice } from "@/components/states";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { formatByUnit } from "@/lib/format";

const SPECS: MetricSpec[] = [
  { metric: "ad_spend" },
  { metric: "platform_reported_revenue" },
  { metric: "platform_roas" },
  { metric: "contribution_after_marketing" },
  { metric: "ad_spend", groupBy: "channel" },
  { metric: "platform_reported_revenue", groupBy: "channel" },
  { metric: "platform_roas", groupBy: "channel" },
  { metric: "contribution_after_marketing", groupBy: "channel" },
];

const NOT_CAUSAL = "Platform-reported, not causal";

export default function MarketingPage() {
  const state = useMetrics(SPECS);
  return (
    <>
      <PageHeader
        title="Marketing"
        description="Ad platform figures are shown for tactical reference only. Modelled contribution comes from orders, not ad clicks."
      />
      <Boundary state={state} onRetry={state.reload} rows={5}>
        {(r) => {
          const platforms = r[4].groups.map((g) => g.key);
          const byKey = (res: (typeof r)[number], k: string) => res.groups.find((g) => g.key === k)?.value ?? null;
          return (
            <div className="space-y-6">
              <DataQualityNotice results={r} />
              <section aria-label="Marketing totals" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <KpiCard title="Ad spend" result={r[0]} />
                <KpiCard title="Platform-reported revenue" result={r[1]} note={NOT_CAUSAL} />
                <KpiCard title="Platform ROAS" result={r[2]} note={NOT_CAUSAL} />
                <KpiCard title="Modelled contribution after marketing" result={r[3]} note="From orders and the metric layer" />
              </section>
              <Card>
                <CardHeader>
                  <CardTitle className="flex flex-wrap items-center gap-2">
                    By ad platform <Badge tone="info">{NOT_CAUSAL}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="overflow-x-auto">
                  <table className="w-full min-w-[32rem] text-sm">
                    <caption className="sr-only">Ad spend, platform-reported revenue and ROAS by platform. Platform-reported, not causal.</caption>
                    <thead>
                      <tr className="border-b border-border text-left text-xs text-muted-foreground">
                        <th scope="col" className="py-1 font-medium">Platform</th>
                        <th scope="col" className="py-1 text-right font-medium">Ad spend</th>
                        <th scope="col" className="py-1 text-right font-medium">Platform-reported revenue</th>
                        <th scope="col" className="py-1 text-right font-medium">Platform ROAS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {platforms.map((k) => (
                        <tr key={k} className="border-b border-border/50 last:border-0">
                          <th scope="row" className="py-1 text-left font-normal">{labelForKey(k)}</th>
                          <td className="py-1 text-right tabular-nums">{formatByUnit(byKey(r[4], k), r[4].unit)}</td>
                          <td className="py-1 text-right tabular-nums">{formatByUnit(byKey(r[5], k), r[5].unit)}</td>
                          <td className="py-1 text-right tabular-nums">{formatByUnit(byKey(r[6], k), r[6].unit)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CardContent>
              </Card>
              <GroupBreakdown
                title="Modelled contribution after marketing, by sales channel"
                caption="Causal-first: derived from orders, not platform attribution."
                result={r[7]}
                keyLabel="Channel"
              />
            </div>
          );
        }}
      </Boundary>
    </>
  );
}
