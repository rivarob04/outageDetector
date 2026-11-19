import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/store";
import type { AreaIsp, AreaOutage, IspAreasResp } from "../api/types";
import { Glyph } from "../components/Status";
import { dateTime, duration } from "../lib/format";

const WINDOWS = [
  [1, "24 hours"],
  [7, "7 days"],
  [30, "30 days"],
] as const;

export default function IspAreas() {
  const [days, setDays] = useState<number>(7);
  const { data, reload } = useApi<IspAreasResp>(`/api/v1/isp-areas?days=${days}`, [days]);
  useEffect(() => {
    const t = setInterval(reload, 60_000);
    return () => clearInterval(t);
  }, [reload]);

  return (
    <div>
      <h1 className="wide mb-1 mt-0 text-3xl font-extrabold">Local ISPs</h1>
      <p className="mb-4 mt-0 max-w-[70ch]" style={{ color: "var(--muted)" }}>
        Outages each internet provider has reported in your postal code areas. One chart per area, one row per provider.
      </p>
      <div role="group" aria-label="Period" className="mb-5 flex flex-wrap gap-1.5">
        {WINDOWS.map(([d, label]) => (
          <button key={d} className="chip" aria-pressed={days === d} onClick={() => setDays(d)}>
            {label}
          </button>
        ))}
      </div>
      {!data ? (
        <p style={{ color: "var(--muted)" }}>Loading…</p>
      ) : data.postal_codes.length === 0 ? (
        <Empty>
          Add your postal codes in <Link to="/settings">Settings → Monitoring</Link> (for example <code>N8W 1H4, N8N 2L9, N9J 1A1</code>). Each area then
          gets its own chart here.
        </Empty>
      ) : data.areas[0]?.isps.length === 0 ? (
        <Empty>
          Watch Bell, Cogeco or TekSavvy from <Link to="/catalog">Add services</Link>. Their outage reports name the areas they affect, so they can be
          charted by area.
        </Empty>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {data.areas.map((a) => (
            <AreaPanel key={a.area} area={a.area} codes={data.postal_codes.filter((c) => c.startsWith(a.area))} isps={a.isps} from={data.from} to={data.to} />
          ))}
        </div>
      )}
      {data && data.other_isps.length > 0 && (
        <p className="mt-5 max-w-[70ch] text-sm" style={{ color: "var(--muted)" }}>
          {listNames(data.other_isps.map((i) => i.name))} publish no outage reports, so Pulse can only check {data.other_isps.length === 1 ? "its" : "their"}{" "}
          servers from your network. Those checks aren't tied to an area and appear on the <Link to="/">Status</Link> page.
        </p>
      )}
    </div>
  );
}

function listNames(names: string[]): string {
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="panel max-w-[70ch] rounded-lg p-5">
      <p className="m-0">{children}</p>
    </div>
  );
}

function AreaPanel({ area, codes, isps, from, to }: { area: string; codes: string[]; isps: AreaIsp[]; from: string; to: string }) {
  const t0 = Date.parse(from);
  const t1 = Date.parse(to);
  const down = isps.filter((i) => i.state !== "operational");
  const outages = isps
    .flatMap((i) => i.outages.map((o) => ({ ...o, isp: i.name })))
    .sort((a, b) => Date.parse(b.started_at ?? "") - Date.parse(a.started_at ?? ""));
  return (
    <section className="panel rounded-lg p-5" aria-labelledby={`area-${area}`}>
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={`area-${area}`} className="wide m-0 text-2xl font-extrabold tracking-wide">
          {area}
        </h2>
        <span className="narrow text-xs" style={{ color: "var(--muted)" }}>
          {codes.filter((c) => c.length > 3).join(" · ") || "whole area"}
        </span>
        <span className={`ml-auto inline-flex items-center gap-1.5 text-sm font-semibold ${down.length ? "st-major_outage" : "st-operational"}`}>
          <Glyph state={down.length ? "major_outage" : "operational"} />
          {down.length ? `${down.map((d) => d.name).join(", ")}: outage now` : "No reported outages now"}
        </span>
      </header>
      <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2">
        {isps.map((i) => (
          <Row key={i.service_id} isp={i} t0={t0} t1={t1} />
        ))}
        <span />
        <Axis t0={t0} t1={t1} />
      </div>
      {outages.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-semibold">
            {outages.length} outage{outages.length === 1 ? "" : "s"} in this period
          </summary>
          <ul className="m-0 mt-2 grid gap-1.5 p-0 text-sm">
            {outages.map((o) => (
              <li key={`${o.isp}-${o.id}`} className="list-none">
                <span className="font-semibold">{o.isp}</span> {o.title}
                <span className="narrow block text-xs" style={{ color: "var(--muted)" }}>
                  {dateTime(o.started_at)} → {o.resolved_at ? dateTime(o.resolved_at) : "ongoing"}
                  {o.started_at && ` (${duration((o.resolved_at ? Date.parse(o.resolved_at) : t1) - Date.parse(o.started_at))})`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function Row({ isp, t0, t1 }: { isp: AreaIsp; t0: number; t1: number }) {
  const span = Math.max(1, t1 - t0);
  const summary =
    isp.outages.length === 0 ? "no outages" : `${isp.outages.length} outage${isp.outages.length === 1 ? "" : "s"}, ${duration(isp.down_s * 1000)} down`;
  return (
    <>
      <Link to={`/service/${isp.service_id}`} className="inline-flex min-w-0 items-center gap-1.5 text-sm font-semibold no-underline">
        <Glyph state={isp.state} size={10} />
        <span className="truncate">{isp.name}</span>
      </Link>
      <div>
        <div
          className="relative h-6 overflow-hidden rounded st-operational"
          style={{ background: "color-mix(in srgb, var(--st) 14%, transparent)" }}
          role="img"
          aria-label={`${isp.name}: ${summary}`}
        >
          {isp.outages.map((o) => (
            <Segment key={o.id} o={o} t0={t0} t1={t1} span={span} />
          ))}
        </div>
        <span className="narrow text-xs" style={{ color: "var(--muted)" }}>
          {summary}
        </span>
      </div>
    </>
  );
}

function Segment({ o, t0, t1, span }: { o: AreaOutage; t0: number; t1: number; span: number }) {
  const s = Math.max(t0, Date.parse(o.started_at ?? ""));
  const e = Math.min(t1, o.resolved_at ? Date.parse(o.resolved_at) : t1);
  if (Number.isNaN(s) || e < t0) return null;
  const left = ((s - t0) / span) * 100;
  // Keep short outages visible: at least ~3px wide.
  const width = Math.max(((e - s) / span) * 100, 0.6);
  const open = !o.resolved_at;
  return (
    <span
      className="st-major_outage absolute inset-y-0"
      style={{
        left: `${left}%`,
        width: `${width}%`,
        // Hatching marks outages without relying on colour alone.
        background: "repeating-linear-gradient(135deg, var(--st) 0 5px, color-mix(in srgb, var(--st) 70%, black) 5px 7px)",
        borderRight: open ? "3px solid var(--text)" : undefined,
      }}
      title={`${o.title}\n${dateTime(o.started_at)} → ${open ? "ongoing" : dateTime(o.resolved_at)}`}
    />
  );
}

function Axis({ t0, t1 }: { t0: number; t1: number }) {
  const hours = (t1 - t0) / 3_600_000;
  // Even steps: 6 h for a day, 1 day (as a weekday) for a week, 5 days for a month.
  const n = hours <= 30 ? 4 : hours <= 24 * 8 ? 7 : 6;
  const ticks = Array.from({ length: n + 1 }, (_, k) => t0 + ((t1 - t0) * k) / n);
  const fmt = (t: number) =>
    hours <= 30
      ? new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
      : hours <= 24 * 8
        ? new Date(t).toLocaleDateString(undefined, { weekday: "short" })
        : new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (
    <div className="narrow flex justify-between text-[11px]" style={{ color: "var(--muted)" }} aria-hidden>
      {ticks.map((t, k) => (
        <span key={t}>{k === n ? "now" : fmt(t)}</span>
      ))}
    </div>
  );
}
