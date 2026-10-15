"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { Boundary, InsufficientData, LoadingBlock } from "@/components/states";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { BriefStatement } from "@/lib/api";
import { formatIST, formatPaiseCompact, formatPercent } from "@/lib/format";
import { MOCK_ORG_INSUFFICIENT } from "@/lib/mocks";

function Statements({ items }: { items: BriefStatement[] }) {
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {items.map((s) => (
        <li key={s.text}>
          {s.text} {s.call_ids.map((c) => <code key={c} className="mr-1 rounded bg-muted px-1 text-xs">[{c}]</code>)}
        </li>
      ))}
    </ul>
  );
}

function BriefView() {
  const { api, orgId } = useApp();
  const scenario = useSearchParams().get("scenario");
  const effectiveOrg = api.isMock && scenario === "insufficient" ? MOCK_ORG_INSUFFICIENT : orgId;
  const state = useAsync(effectiveOrg ? () => api.getBrief(effectiveOrg) : null, `brief|${effectiveOrg}`);
  return (
    <>
    {api.isMock && (
      <p className="text-xs text-muted-foreground">
        Sample scenario:{" "}
        <Link className="underline" href="/intelligence/brief">brief</Link> |{" "}
        <Link className="underline" href="/intelligence/brief?scenario=insufficient">insufficient data</Link>
      </p>
    )}
    <Boundary state={state} onRetry={state.reload} rows={6}>
      {(b) =>
        b.status === "INSUFFICIENT_DATA" ? (
          <div className="space-y-3">
            <InsufficientData reason={b.reason} fallback={b.fallback} />
            <p className="text-sm text-muted-foreground">No budget recommendation, interval or confidence is shown because the evidence is too weak.</p>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-xs text-muted-foreground">Generated {formatIST(b.generated_at)}. Every figure cites a stored tool call.</p>
            <Card>
              <CardHeader><CardTitle>What happened</CardTitle></CardHeader>
              <CardContent><Statements items={b.what_happened} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Why</CardTitle></CardHeader>
              <CardContent><Statements items={b.why} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Top actions</CardTitle></CardHeader>
              <CardContent>
                <ol className="space-y-4">
                  {b.actions.map((a) => (
                    <li key={a.title} className="space-y-1 text-sm">
                      <p className="flex flex-wrap items-center gap-2 font-medium">
                        {a.title}
                        <Badge tone={a.confidence === "high" ? "success" : a.confidence === "medium" ? "info" : "warning"}>{a.confidence} confidence</Badge>
                      </p>
                      <p className="text-muted-foreground">{a.rationale}</p>
                      <p>
                        Expected impact: <strong>{formatPaiseCompact(a.expected_impact_paise)}</strong>
                        {a.ci_low_paise !== null && a.ci_high_paise !== null ? (
                          <> (80% interval {formatPaiseCompact(a.ci_low_paise)} to {formatPaiseCompact(a.ci_high_paise)})</>
                        ) : (
                          <span className="text-muted-foreground"> (no interval available)</span>
                        )}
                        {a.prob_beats_current !== null && <> , probability of beating current: {formatPercent(a.prob_beats_current * 100, { digits: 0 })}</>}
                      </p>
                      <p>{a.call_ids.map((c) => <code key={c} className="mr-1 rounded bg-muted px-1 text-xs">[{c}]</code>)}</p>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Experiment recommendation</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-sm">
                {b.experiment ? (
                  <>
                    <p className="font-medium">{b.experiment.name}</p>
                    <p><span className="text-muted-foreground">Hypothesis: </span>{b.experiment.hypothesis}</p>
                    <p><span className="text-muted-foreground">Design: </span>{b.experiment.design}</p>
                  </>
                ) : (
                  <p className="text-muted-foreground">No experiment recommended this week.</p>
                )}
              </CardContent>
            </Card>
          </div>
        )
      }
    </Boundary>
    </>
  );
}

export default function BriefPage() {
  return (
    <>
      <PageHeader title="Weekly AI brief" description="What happened, why, and where the next rupee should go." />
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <BriefView />
      </Suspense>
    </>
  );
}
