"use client";

import { useState, type FormEvent } from "react";
import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { Boundary, EmptyState } from "@/components/states";
import { Button, Card, CardContent, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { formatCalendarDate, formatPaise, parseRupeesToPaise } from "@/lib/format";

export default function CogsPage() {
  const { api, orgId } = useApp();
  const state = useAsync(orgId ? () => api.listSkuCogs(orgId) : null, `cogs|${orgId}`);
  const [sku, setSku] = useState("");
  const [cogs, setCogs] = useState("");
  const [date, setDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(null);
    const paise = parseRupeesToPaise(cogs);
    if (!orgId) return setError("Organisation not resolved yet.");
    if (!sku.trim()) return setError("SKU is required.");
    if (paise === null) return setError("Enter COGS in rupees, up to 2 decimals (for example 235.50).");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return setError("Choose an effective date.");
    setBusy(true);
    try {
      await api.upsertSkuCogs({ org_id: orgId, sku: sku.trim(), effective_from: date, cogs_paise: paise });
      setSaved(`Saved ${sku.trim()} from ${formatCalendarDate(date)}.${api.isMock ? " (Sample mode: not persisted.)" : ""}`);
      setSku("");
      setCogs("");
      state.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="COGS editor"
        description="Cost of goods per SKU, effective-dated. The latest entry on or before an order date applies. Saving the same SKU and date replaces the value."
      />
      <Card>
        <CardHeader><CardTitle>Add or update COGS</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end" noValidate>
            <div className="space-y-1">
              <label htmlFor="sku" className="text-sm font-medium">SKU</label>
              <Input id="sku" value={sku} onChange={(e) => setSku(e.target.value)} placeholder="SKU-SERUM-30" />
            </div>
            <div className="space-y-1">
              <label htmlFor="cogs" className="text-sm font-medium">COGS per unit (INR)</label>
              <Input id="cogs" inputMode="decimal" value={cogs} onChange={(e) => setCogs(e.target.value)} placeholder="235.00" />
            </div>
            <div className="space-y-1">
              <label htmlFor="date" className="text-sm font-medium">Effective from</label>
              <Input id="date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
            <Button type="submit" disabled={busy}>{busy ? "Saving..." : "Save"}</Button>
          </form>
          <div aria-live="polite" className="mt-2 min-h-5 text-sm">
            {error && <p role="alert" className="text-negative">{error}</p>}
            {saved && <p className="text-positive">{saved}</p>}
          </div>
        </CardContent>
      </Card>
      <Boundary state={state} onRetry={state.reload} rows={4}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="No COGS entered yet" hint="Without COGS, contribution metrics report missing-data caveats instead of guessing." />
          ) : (
            <Card>
              <CardContent className="overflow-x-auto pt-4">
                <table className="w-full min-w-[24rem] text-sm">
                  <caption className="sr-only">SKU COGS history</caption>
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-muted-foreground">
                      <th scope="col" className="py-1 font-medium">SKU</th>
                      <th scope="col" className="py-1 font-medium">Effective from</th>
                      <th scope="col" className="py-1 text-right font-medium">COGS / unit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows
                      .slice()
                      .sort((a, b) => a.sku.localeCompare(b.sku) || b.effective_from.localeCompare(a.effective_from))
                      .map((r) => (
                        <tr key={`${r.sku}|${r.effective_from}`} className="border-b border-border/50 last:border-0">
                          <th scope="row" className="py-1 text-left font-normal">{r.sku}</th>
                          <td className="py-1">{formatCalendarDate(r.effective_from)}</td>
                          <td className="py-1 text-right tabular-nums">{formatPaise(r.cogs_paise, { showPaise: true })}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )
        }
      </Boundary>
    </>
  );
}
