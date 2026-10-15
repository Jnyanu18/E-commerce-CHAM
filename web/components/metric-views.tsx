"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { EmptyState, InsufficientData } from "@/components/states";
import type { MetricResult } from "@/lib/api";
import { formatByUnit, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

const KEY_LABELS: Record<string, string> = {
  cod: "COD",
  prepaid: "Prepaid",
  metro: "Metro",
  tier_1: "Tier 1",
  tier_2: "Tier 2",
  tier_3: "Tier 3",
  unknown: "Unknown",
  google_ads: "Google Ads",
  meta: "Meta",
};
export function labelForKey(key: string): string {
  return KEY_LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** A headline number from a MetricResult; shows insufficient-data instead of zero when value is null. */
export function KpiCard({
  title,
  result,
  deltaPct,
  deltaLabel = "vs prior week",
  note,
}: {
  title: string;
  result: MetricResult;
  deltaPct?: number | null;
  deltaLabel?: string;
  note?: string;
}) {
  const insufficient = result.value === null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold tabular-nums" aria-label={`${title}: ${formatByUnit(result.value, result.unit)}`}>
          {formatByUnit(result.value, result.unit)}
        </p>
        {insufficient ? (
          <p className="mt-1 text-xs font-medium text-warning-fg">
            <Badge tone="warning">Insufficient data</Badge>
          </p>
        ) : deltaPct !== undefined ? (
          <p className="mt-1 text-xs text-muted-foreground">
            <span
              className={cn(
                "font-medium",
                deltaPct === null ? "" : deltaPct >= 0 ? "text-positive" : "text-negative",
              )}
            >
              {deltaPct === null ? "No comparison" : `${deltaPct >= 0 ? "▲" : "▼"} ${formatPercent(deltaPct, { signed: true })}`}
            </span>{" "}
            {deltaPct !== null && deltaLabel}
          </p>
        ) : null}
        {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
      </CardContent>
    </Card>
  );
}

/** Bar chart + accessible data table for a grouped MetricResult. Values are rendered as returned. */
export function GroupBreakdown({
  title,
  result,
  caption,
  keyLabel = "Group",
  labelFor = labelForKey,
}: {
  title: string;
  result: MetricResult;
  caption?: string;
  keyLabel?: string;
  labelFor?: (key: string) => string;
}) {
  const rows = result.groups.map((g) => ({ key: g.key, label: labelFor(g.key), value: g.value }));
  const hasValues = rows.some((r) => r.value !== null);
  const chartData = rows.map((r) => ({ ...r, chartValue: r.value ?? 0 }));
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
      </CardHeader>
      <CardContent>
        {result.data_quality.status === "insufficient" ? (
          <InsufficientData reason={result.data_quality.issues.join("; ") || undefined} />
        ) : rows.length === 0 || !hasValues ? (
          <EmptyState title="No data for this period" hint="Try a longer date range." />
        ) : (
          <>
            <div className="h-56 w-full" role="img" aria-label={`${title} bar chart. Values are listed in the table below.`}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <CartesianGrid horizontal={false} stroke="var(--border)" />
                  <XAxis type="number" tickFormatter={(v: number) => formatByUnit(v, result.unit)} stroke="var(--muted-foreground)" fontSize={11} />
                  <YAxis type="category" dataKey="label" width={96} stroke="var(--muted-foreground)" fontSize={12} />
                  <Tooltip
                    formatter={(v) => [formatByUnit(Number(v), result.unit), result.metric]}
                    contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                  <Bar dataKey="chartValue" radius={[0, 3, 3, 0]} isAnimationActive={false}>
                    {chartData.map((d) => (
                      <Cell key={d.key} fill={(d.value ?? 0) < 0 ? "var(--chart-neg)" : "var(--chart-1)"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <table className="mt-3 w-full text-sm">
              <caption className="sr-only">{title}</caption>
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-1 font-medium">{keyLabel}</th>
                  <th scope="col" className="py-1 text-right font-medium">{result.metric.replace(/_/g, " ")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-b border-border/50 last:border-0">
                    <th scope="row" className="py-1 text-left font-normal">{r.label}</th>
                    <td className={cn("py-1 text-right tabular-nums", (r.value ?? 0) < 0 && "text-negative")}>
                      {formatByUnit(r.value, result.unit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <p className="mt-2 text-xs text-muted-foreground">{result.definition}</p>
      </CardContent>
    </Card>
  );
}
