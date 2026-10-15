"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { BriefDocument } from "@/components/brief-view";
import { Boundary, EmptyState, LoadingBlock } from "@/components/states";
import { MOCK_ORG_FALLBACK, MOCK_ORG_INSUFFICIENT, MOCK_ORG_NO_BRIEF } from "@/lib/mocks";

const SCENARIOS: Record<string, string> = {
  insufficient: MOCK_ORG_INSUFFICIENT,
  fallback: MOCK_ORG_FALLBACK,
  none: MOCK_ORG_NO_BRIEF,
};

function BriefView() {
  const { api, orgId } = useApp();
  const scenario = useSearchParams().get("scenario");
  const effectiveOrg = api.isMock && scenario && SCENARIOS[scenario] ? SCENARIOS[scenario] : orgId;
  const state = useAsync(effectiveOrg ? () => api.getBrief(effectiveOrg) : null, `brief|${effectiveOrg}`);
  return (
    <>
      {api.isMock && (
        <p className="text-xs text-muted-foreground">
          Sample scenario: <Link className="underline" href="/intelligence/brief">brief</Link> |{" "}
          <Link className="underline" href="/intelligence/brief?scenario=fallback">fallback</Link> |{" "}
          <Link className="underline" href="/intelligence/brief?scenario=insufficient">insufficient data</Link> |{" "}
          <Link className="underline" href="/intelligence/brief?scenario=none">no brief yet</Link>
        </p>
      )}
      <Boundary state={state} onRetry={state.reload} rows={6}>
        {(b) =>
          b.status === "none" ? (
            <EmptyState title="No brief yet" hint={b.reason} />
          ) : (
            <BriefDocument brief={b} />
          )
        }
      </Boundary>
    </>
  );
}

export default function BriefPage() {
  return (
    <>
      <PageHeader title="Weekly AI brief" description="What happened, why, and where the next rupee should go. Every figure links to the stored tool call it came from." />
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <BriefView />
      </Suspense>
    </>
  );
}
