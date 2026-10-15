"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, Brain, Coins, LayoutDashboard, LogOut, Megaphone, Plug } from "lucide-react";
import { AppProvider, useApp, type PresetDays } from "@/components/app-provider";
import { Badge, Button } from "@/components/ui/primitives";
import { isMockMode } from "@/lib/api";
import { formatCalendarDate } from "@/lib/format";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/profitability", label: "Profitability", icon: BarChart3 },
  { href: "/marketing", label: "Marketing", icon: Megaphone },
  { href: "/intelligence", label: "Anomalies", icon: Brain },
  { href: "/intelligence/brief", label: "AI brief", icon: Brain },
  { href: "/cogs", label: "COGS editor", icon: Coins },
  { href: "/integrations", label: "Integrations", icon: Plug },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href;
}

/** Session state: "required" redirects to /login in live mode; mock mode never blocks. */
function useSession() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const live = !isMockMode();

  useEffect(() => {
    const sb = getSupabase();
    if (!sb) {
      setChecked(true);
      return;
    }
    let active = true;
    sb.auth.getSession().then(({ data }) => {
      if (!active) return;
      setEmail(data.session?.user.email ?? null);
      setChecked(true);
      if (live && !data.session) router.replace("/login");
    });
    const { data: sub } = sb.auth.onAuthStateChange((_e, session) => setEmail(session?.user.email ?? null));
    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, [live, router]);

  const blocked = live && (!checked || (isSupabaseConfigured() && !email));
  return { email, blocked };
}

function PeriodSelector() {
  const { days, setDays, range } = useApp();
  return (
    <div className="flex items-center gap-2 text-sm">
      <label htmlFor="period" className="text-muted-foreground">
        Period
      </label>
      <select
        id="period"
        value={days}
        onChange={(e) => setDays(Number(e.target.value) as PresetDays)}
        className="h-9 rounded-md border border-border bg-card px-2"
      >
        <option value={7}>Last 7 days</option>
        <option value={30}>Last 30 days</option>
        <option value={90}>Last 90 days</option>
      </select>
      {range && (
        <span className="hidden text-muted-foreground md:inline">
          {formatCalendarDate(range.start)} to {formatCalendarDate(range.end)} (IST)
        </span>
      )}
    </div>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { api, orgError } = useApp();
  const { email, blocked } = useSession();

  async function signOut() {
    await getSupabase()?.auth.signOut();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-card focus:p-2">
        Skip to content
      </a>
      <aside className="border-b border-border bg-card md:w-56 md:shrink-0 md:border-b-0 md:border-r">
        <div className="px-4 py-3 text-base font-semibold">[PRODUCT_NAME]</div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={isActive(pathname, href) ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-muted",
                isActive(pathname, href) && "bg-muted font-medium",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-4 py-2">
          <div className="flex items-center gap-3">
            <PeriodSelector />
            {api.isMock && <Badge tone="warning">Sample data</Badge>}
          </div>
          <div className="flex items-center gap-2 text-sm">
            {email ? (
              <>
                <span className="text-muted-foreground">{email}</span>
                <Button variant="outline" onClick={signOut} aria-label="Sign out">
                  <LogOut className="h-4 w-4" aria-hidden="true" /> Sign out
                </Button>
              </>
            ) : isSupabaseConfigured() ? (
              <Button asChild variant="outline">
                <Link href="/login">Sign in</Link>
              </Button>
            ) : (
              <span className="text-muted-foreground">Auth not configured</span>
            )}
          </div>
        </header>
        {api.isMock && (
          <div role="note" className="border-b border-border bg-warning-bg px-4 py-2 text-sm text-warning-fg">
            <strong>Sample data.</strong> Figures on every page are mock responses from the front-end mock layer, not your business data.
          </div>
        )}
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 space-y-6 p-4 md:p-6">
          {orgError && (
            <p role="alert" className="rounded-md border border-negative/40 bg-negative/10 p-3 text-sm">
              {orgError}
            </p>
          )}
          {blocked ? <p role="status">Checking your session...</p> : children}
        </main>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AppProvider>
      <Shell>{children}</Shell>
    </AppProvider>
  );
}

export function PageHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div>
      <h1 className="text-xl font-semibold">{title}</h1>
      {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}
