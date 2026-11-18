import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, setCsrf } from "./client";
import type { Me, Summary } from "./types";

interface PulseCtx {
  summary: Summary | null;
  error: string | null;
  me: Me | null;
  live: boolean;
  changed: Set<string>;
  refresh: () => Promise<void>;
  refreshMe: () => Promise<void>;
  isAdmin: boolean;
}

const Ctx = createContext<PulseCtx | null>(null);

export function PulseProvider({ children }: { children: ReactNode }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [live, setLive] = useState(false);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const pending = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSummary(await api<Summary>("/api/v1/summary"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refreshMe = useCallback(async () => {
    const m = await api<Me>("/api/v1/auth/me");
    setCsrf(m.csrf);
    setMe(m);
  }, []);

  const scheduleRefresh = useCallback(() => {
    if (pending.current !== null) return;
    pending.current = window.setTimeout(() => {
      pending.current = null;
      void refresh();
    }, 800);
  }, [refresh]);

  useEffect(() => {
    void refreshMe().then(refresh);
    const poll = window.setInterval(() => void refresh(), 60_000);
    return () => window.clearInterval(poll);
  }, [refresh, refreshMe]);

  useEffect(() => {
    if (!me || (!me.public_view && !me.user)) return;
    const es = new EventSource("/api/v1/events");
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("state", (ev) => {
      const data = JSON.parse((ev as MessageEvent).data);
      setSummary((s) =>
        s
          ? {
              ...s,
              services: s.services.map((svc) =>
                svc.id === data.service_id
                  ? { ...svc, state: data.state, scope: data.scope, reasons: data.reasons, advisories: data.advisories, since: data.since }
                  : svc,
              ),
            }
          : s,
      );
      setChanged((c) => new Set(c).add(data.service_id));
      window.setTimeout(() => setChanged((c) => {
        const n = new Set(c);
        n.delete(data.service_id);
        return n;
      }), 4000);
      scheduleRefresh();
    });
    for (const t of ["incident", "connectivity", "agent"]) es.addEventListener(t, scheduleRefresh);
    return () => es.close();
  }, [me, scheduleRefresh]);

  const value = useMemo(
    () => ({ summary, error, me, live, changed, refresh, refreshMe, isAdmin: Boolean(me?.user) }),
    [summary, error, me, live, changed, refresh, refreshMe],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePulse(): PulseCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePulse outside PulseProvider");
  return v;
}

/** Fetch helper for page-level data with refetch on SSE-driven summary changes. */
export function useApi<T>(path: string | null, deps: unknown[] = []): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    api<T>(path)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}
