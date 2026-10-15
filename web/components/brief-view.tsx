"use client";

import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Info, X } from "lucide-react";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { BriefCall } from "@/lib/api";
import { formatByUnit, formatCalendarDate, formatIST } from "@/lib/format";
import { parseMarkdown, type Inline } from "@/lib/markdown";

function InlineView({ nodes, calls, onCite, active }: { nodes: Inline[]; calls: Map<string, BriefCall>; onCite: (id: string) => void; active: string | null }) {
  return (
    <>
      {nodes.map((n, i) => {
        if (n.t === "text") return <Fragment key={i}>{n.v}</Fragment>;
        if (n.t === "bold") return <strong key={i}>{n.v}</strong>;
        if (n.t === "code") return <code key={i} className="rounded bg-muted px-1 text-xs">{n.v}</code>;
        if (!calls.has(n.id)) return <Fragment key={i}>[{n.id}]</Fragment>; // unresolved: shown as plain text, never as a source
        return (
          <button
            key={i}
            type="button"
            onClick={() => onCite(n.id)}
            aria-pressed={active === n.id}
            aria-label={`Show source ${n.id}`}
            className="mx-0.5 rounded-full border border-border bg-info-bg px-1.5 align-baseline text-xs font-medium text-info-fg hover:underline"
          >
            {n.id}
          </button>
        );
      })}
    </>
  );
}

function resultSummary(result: unknown): { value: string | null; status: string | null; issues: string[]; raw: string } {
  const r = (typeof result === "object" && result !== null ? result : {}) as Record<string, unknown>;
  const dq = (typeof r.data_quality === "object" && r.data_quality !== null ? r.data_quality : {}) as Record<string, unknown>;
  const value = typeof r.value === "number" ? formatByUnit(r.value, String(r.unit ?? "")) : r.value === null ? "no value (insufficient)" : null;
  const status = typeof dq.status === "string" ? dq.status : typeof r.status === "string" ? r.status : null;
  const issues = Array.isArray(dq.issues) ? dq.issues.map(String) : typeof r.reason === "string" ? [r.reason] : [];
  let raw = "";
  try {
    raw = JSON.stringify(result, null, 2) ?? "";
  } catch {
    raw = String(result);
  }
  return { value, status, issues, raw: raw.length > 1500 ? raw.slice(0, 1500) + "\n..." : raw };
}

/** The "Source" panel: built only from the stored tool call that the citation resolves to. */
export function SourcePanel({ call, onClose }: { call: BriefCall; onClose: () => void }) {
  const s = resultSummary(call.result);
  const a = call.args as Record<string, unknown>;
  const period = typeof a.start === "string" && typeof a.end === "string" ? `${formatCalendarDate(a.start)} to ${formatCalendarDate(a.end)}` : null;
  const filters = a.filters && typeof a.filters === "object" && Object.keys(a.filters).length ? JSON.stringify(a.filters) : "none";
  return (
    <aside aria-label={`Source ${call.call_id}`} className="rounded-lg border border-border bg-card p-4 text-sm lg:sticky lg:top-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-semibold">Source [{call.call_id}]</h2>
        <button type="button" onClick={onClose} aria-label="Close source panel" className="rounded p-1 hover:bg-muted">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">Tool</dt><dd>{call.tool}</dd>
        {typeof a.metric === "string" && (<><dt className="text-muted-foreground">Metric</dt><dd>{a.metric}</dd></>)}
        {period && (<><dt className="text-muted-foreground">Period</dt><dd>{period}</dd></>)}
        {typeof a.group_by === "string" && (<><dt className="text-muted-foreground">Group by</dt><dd>{a.group_by}</dd></>)}
        <dt className="text-muted-foreground">Filters</dt><dd>{filters}</dd>
        {s.value && (<><dt className="text-muted-foreground">Value</dt><dd className="font-medium">{s.value}</dd></>)}
        {s.status && (<><dt className="text-muted-foreground">Status</dt><dd>{s.status}</dd></>)}
        <dt className="text-muted-foreground">Called</dt><dd>{formatIST(call.timestamp)}</dd>
      </dl>
      {!call.ok && <p role="alert" className="mt-2 text-negative">This call failed: {JSON.stringify(call.error)}</p>}
      {s.issues.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-warning-fg">{s.issues.map((i) => <li key={i}>{i}</li>)}</ul>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-muted-foreground">Stored result</summary>
        <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-2 text-xs">{s.raw}</pre>
      </details>
    </aside>
  );
}

export function BriefMarkdown({
  markdown, calls, onCite, active,
}: { markdown: string; calls: Map<string, BriefCall>; onCite: (id: string) => void; active: string | null }) {
  const blocks = useMemo(() => parseMarkdown(markdown), [markdown]);
  const iv = (nodes: Inline[]): ReactNode => <InlineView nodes={nodes} calls={calls} onCite={onCite} active={active} />;
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {blocks.map((b, i) => {
        if (b.t === "heading") {
          const cls = b.level === 1 ? "text-lg font-semibold" : "text-base font-semibold";
          return b.level === 1 ? <h2 key={i} className={cls}>{iv(b.inline)}</h2> : <h3 key={i} className={cls}>{iv(b.inline)}</h3>;
        }
        if (b.t === "paragraph") return <p key={i}>{iv(b.inline)}</p>;
        const Tag = b.t === "ul" ? "ul" : "ol";
        return (
          <Tag key={i} className={b.t === "ul" ? "list-disc space-y-1 pl-5" : "list-decimal space-y-1 pl-5"}>
            {b.items.map((it, j) => <li key={j}>{iv(it)}</li>)}
          </Tag>
        );
      })}
    </div>
  );
}

export function BriefDocument({
  brief,
}: {
  brief: { week_start: string; generated_at: string | null; emailed_at: string | null; mode: "narrative" | "fallback" | null; markdown: string; calls: BriefCall[] };
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const callMap = useMemo(() => new Map(brief.calls.map((c) => [c.call_id, c])), [brief.calls]);
  const active = activeId ? callMap.get(activeId) ?? null : null;
  return (
    <div className="space-y-4">
      {brief.mode === "fallback" && (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-border bg-warning-bg p-3 text-sm text-warning-fg">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p><strong>Data-only summary:</strong> the AI narrative failed validation.</p>
        </div>
      )}
      <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge tone="neutral">Week of {formatCalendarDate(brief.week_start)}</Badge>
        {brief.generated_at && <span>Generated {formatIST(brief.generated_at)}</span>}
        {brief.emailed_at && <span>Emailed {formatIST(brief.emailed_at)}</span>}
        <span>Select a numbered citation to see the stored tool call behind it.</span>
      </p>
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card>
          <CardHeader><CardTitle>Brief</CardTitle></CardHeader>
          <CardContent><BriefMarkdown markdown={brief.markdown} calls={callMap} onCite={setActiveId} active={activeId} /></CardContent>
        </Card>
        {active ? (
          <SourcePanel call={active} onClose={() => setActiveId(null)} />
        ) : (
          <aside aria-label="Source" className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
            No citation selected.
          </aside>
        )}
      </div>
    </div>
  );
}
