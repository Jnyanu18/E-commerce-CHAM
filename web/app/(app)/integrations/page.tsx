"use client";

import { useApp, useAsync } from "@/components/app-provider";
import { PageHeader } from "@/components/app-shell";
import { Boundary, EmptyState } from "@/components/states";
import { Badge, Card, CardContent } from "@/components/ui/primitives";
import type { IntegrationStatus } from "@/lib/api";
import { formatIST } from "@/lib/format";

const TONE: Record<IntegrationStatus, "success" | "danger" | "warning" | "neutral"> = {
  active: "success",
  error: "danger",
  pending: "warning",
  disconnected: "neutral",
};
const PROVIDER: Record<string, string> = {
  shopify: "Shopify",
  meta: "Meta Ads",
  google_ads: "Google Ads",
  shiprocket: "Shiprocket",
  csv: "CSV import",
};

export default function IntegrationsPage() {
  const { api, orgId } = useApp();
  const state = useAsync(orgId ? () => api.listIntegrations(orgId) : null, `int|${orgId}`);
  return (
    <>
      <PageHeader title="Integrations" description="Read-only sync status. Credentials are never exposed to the browser." />
      <Boundary state={state} onRetry={state.reload} rows={4}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="No integrations connected" hint="Connect a data source to start syncing orders and spend." />
          ) : (
            <Card>
              <CardContent className="overflow-x-auto pt-4">
                <table className="w-full min-w-[40rem] text-sm">
                  <caption className="sr-only">Integration sync status</caption>
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-muted-foreground">
                      <th scope="col" className="py-1 font-medium">Provider</th>
                      <th scope="col" className="py-1 font-medium">Account</th>
                      <th scope="col" className="py-1 font-medium">Status</th>
                      <th scope="col" className="py-1 font-medium">Last sync</th>
                      <th scope="col" className="py-1 font-medium">Backfill</th>
                      <th scope="col" className="py-1 font-medium">Last error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} className="border-b border-border/50 align-top last:border-0">
                        <th scope="row" className="py-2 text-left font-medium">{PROVIDER[r.provider] ?? r.provider}</th>
                        <td className="py-2 text-muted-foreground">{r.account_ref}</td>
                        <td className="py-2"><Badge tone={TONE[r.status]}>{r.status}</Badge></td>
                        <td className="py-2">{r.last_synced_at ? formatIST(r.last_synced_at) : "Never"}</td>
                        <td className="py-2">{r.backfill_done ? "Done" : "Pending"}</td>
                        <td className="py-2 text-negative">{r.last_error ?? ""}</td>
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
