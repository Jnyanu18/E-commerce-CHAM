"use client";

import { useMetrics, type MetricSpec } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { GroupBreakdown } from "@/components/metric-views";
import { Boundary, DataQualityNotice } from "@/components/states";

const SPECS: MetricSpec[] = [
  { metric: "contribution_margin", groupBy: "channel" },
  { metric: "contribution_margin", groupBy: "sku" },
  { metric: "contribution_margin", groupBy: "category" },
  { metric: "contribution_margin", groupBy: "city" },
  { metric: "contribution_margin", groupBy: "city_tier" },
  { metric: "contribution_margin", groupBy: "payment_method" },
  { metric: "net_revenue", groupBy: "payment_method" },
  { metric: "expected_rto_loss", groupBy: "payment_method" },
];

export default function ProfitabilityPage() {
  const state = useMetrics(SPECS);
  return (
    <>
      <PageHeader
        title="Profitability"
        description="Contribution margin after COGS, fees, shipping and return/RTO costs, as computed by the metric layer."
      />
      <Boundary state={state} onRetry={state.reload} rows={6}>
        {(r) => (
          <div className="space-y-6">
            <DataQualityNotice results={r} />
            <div className="grid gap-4 lg:grid-cols-2">
              <GroupBreakdown title="By channel" result={r[0]} keyLabel="Channel" />
              <GroupBreakdown title="By category" result={r[2]} keyLabel="Category" />
            </div>
            <GroupBreakdown title="By SKU" result={r[1]} keyLabel="SKU" labelFor={(k) => k} />
            <div className="grid gap-4 lg:grid-cols-2">
              <GroupBreakdown title="By city" result={r[3]} keyLabel="City" />
              <GroupBreakdown title="By city tier" result={r[4]} keyLabel="Tier" />
            </div>
            <section aria-labelledby="cod-heading" className="space-y-3">
              <h2 id="cod-heading" className="text-base font-semibold">COD vs prepaid</h2>
              <div className="grid gap-4 lg:grid-cols-3">
                <GroupBreakdown title="Net revenue" result={r[6]} keyLabel="Payment" />
                <GroupBreakdown title="Contribution margin" result={r[5]} keyLabel="Payment" />
                <GroupBreakdown title="Expected RTO loss" result={r[7]} keyLabel="Payment" />
              </div>
            </section>
          </div>
        )}
      </Boundary>
    </>
  );
}
