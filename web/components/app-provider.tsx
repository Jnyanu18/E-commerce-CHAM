"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { getApi, type Api, type MetricQuery, type MetricResult } from "@/lib/api";
import { addDays, toISTDateString } from "@/lib/format";

export type PresetDays = 7 | 30 | 90;

interface AppState {
  api: Api;
  orgId: string | null;
  orgError: string | null;
  days: PresetDays;
  setDays: (d: PresetDays) => void;
  /** IST calendar dates; null until mounted (avoids server/client clock mismatch). */
  range: { start: string; end: string } | null;
  /** Last 7 IST days and the 7 before them, for week-over-week display. */
  weeks: { current: { start: string; end: string }; previous: { start: string; end: string } } | null;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const api = useMemo(() => getApi(), []);
  const [orgId, setOrgId] = useState<string | null>(null);
  const [orgError, setOrgError] = useState<string | null>(null);
  const [days, setDays] = useState<PresetDays>(30);
  const [today, setToday] = useState<string | null>(null);

  useEffect(() => {
    setToday(toISTDateString(new Date()));
    api
      .getOrgId()
      .then(setOrgId)
      .catch((e: unknown) => setOrgError(e instanceof Error ? e.message : "Could not resolve organisation"));
  }, [api]);

  const range = useMemo(() => (today ? { start: addDays(today, -(days - 1)), end: today } : null), [today, days]);
  const weeks = useMemo(
    () =>
      today
        ? {
            current: { start: addDays(today, -6), end: today },
            previous: { start: addDays(today, -13), end: addDays(today, -7) },
          }
        : null,
    [today],
  );

  const value = useMemo(() => ({ api, orgId, orgError, days, setDays, range, weeks }), [api, orgId, orgError, days, range, weeks]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside AppProvider");
  return v;
}

export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: Error }
  | { status: "ready"; data: T; error: null };

/** Runs an async loader whenever `key` changes; ignores stale responses. */
export function useAsync<T>(loader: (() => Promise<T>) | null, key: string): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading", data: null, error: null });
  const [nonce, setNonce] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const enabled = loader !== null;

  useEffect(() => {
    const fn = loaderRef.current;
    if (!enabled || !fn) return;
    let cancelled = false;
    setState({ status: "loading", data: null, error: null });
    fn().then(
      (data) => !cancelled && setState({ status: "ready", data, error: null }),
      (e: unknown) => !cancelled && setState({ status: "error", data: null, error: e instanceof Error ? e : new Error(String(e)) }),
    );
    return () => {
      cancelled = true;
    };
  }, [key, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}

export interface MetricSpec {
  metric: MetricQuery["metric"];
  groupBy?: MetricQuery["groupBy"];
  /** "range" = selected period (default); "week" / "prevWeek" = fixed week windows. */
  window?: "range" | "week" | "prevWeek";
}

/** Fetches several metrics in parallel for the selected window. Results align with `specs`. */
export function useMetrics(specs: MetricSpec[]): AsyncState<MetricResult[]> & { reload: () => void } {
  const { api, orgId, range, weeks } = useApp();
  const ready = Boolean(orgId && range && weeks);
  const specKey = JSON.stringify(specs);
  const key = `${orgId}|${range?.start}|${range?.end}|${weeks?.current.start}|${specKey}`;
  const loader = ready
    ? () =>
        Promise.all(
          specs.map((s) => {
            const w = s.window === "week" ? weeks!.current : s.window === "prevWeek" ? weeks!.previous : range!;
            return api.getMetric({ orgId: orgId!, metric: s.metric, groupBy: s.groupBy, start: w.start, end: w.end });
          }),
        )
    : null;
  return useAsync(loader, key);
}
