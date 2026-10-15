"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Info, Inbox } from "lucide-react";
import { Button, Skeleton } from "@/components/ui/primitives";
import type { AsyncState } from "@/components/app-provider";
import type { MetricResult } from "@/lib/api";

export function LoadingBlock({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className="space-y-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-6 w-full" />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-negative/40 bg-negative/10 p-4 text-sm">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-negative" aria-hidden="true" />
      <div className="space-y-2">
        <p className="font-medium">Could not load this data.</p>
        <p className="text-muted-foreground">{error.message}</p>
        {onRetry && (
          <Button variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border p-8 text-center text-sm">
      <Inbox className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
      <p className="font-medium">{title}</p>
      {hint && <p className="text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function InsufficientData({ reason, fallback }: { reason?: string; fallback?: string }) {
  return (
    <div role="status" className="flex items-start gap-3 rounded-md border border-border bg-warning-bg p-4 text-sm text-warning-fg">
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-semibold">Insufficient data</p>
        <p>We do not have enough data to answer this reliably, so no figure or confidence is shown.</p>
        {reason && <p>Reason: {reason}</p>}
        {fallback && <p>Fallback: {fallback} analysis only.</p>}
      </div>
    </div>
  );
}

/** Lists data_quality issues from MetricResults (deduplicated). Renders nothing when all clear. */
export function DataQualityNotice({ results }: { results: MetricResult[] }) {
  const items: { metric: string; status: string; issue: string }[] = [];
  const seen = new Set<string>();
  for (const r of results) {
    if (r.data_quality.status === "ok" && r.data_quality.issues.length === 0) continue;
    const issues = r.data_quality.issues.length ? r.data_quality.issues : [`status: ${r.data_quality.status}`];
    for (const issue of issues) {
      const k = `${r.metric}|${issue}`;
      if (seen.has(k)) continue;
      seen.add(k);
      items.push({ metric: r.metric, status: r.data_quality.status, issue });
    }
  }
  if (items.length === 0) return null;
  return (
    <div role="note" className="rounded-md border border-border bg-warning-bg p-3 text-sm text-warning-fg">
      <p className="mb-1 flex items-center gap-2 font-semibold">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" /> Data quality caveats
      </p>
      <ul className="list-disc space-y-0.5 pl-5">
        {items.map((it) => (
          <li key={`${it.metric}|${it.issue}`}>
            <span className="font-medium">{it.metric}</span> ({it.status}): {it.issue}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Renders loading / error / content for an async state. */
export function Boundary<T>({
  state,
  onRetry,
  rows,
  children,
}: {
  state: AsyncState<T>;
  onRetry?: () => void;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  if (state.status === "loading") return <LoadingBlock rows={rows} />;
  if (state.status === "error") return <ErrorState error={state.error} onRetry={onRetry} />;
  return <>{children(state.data)}</>;
}
