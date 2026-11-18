import { useMemo, useState } from "react";
import type { LatencyPoint, State, UptimeDay } from "../api/types";
import { ms, pct } from "../lib/format";

/** Latency trace drawn like an instrument recording; gaps where checks failed. */
export function Sparkline({ values, state, height = 34 }: { values: (number | null)[]; state: State; height?: number }) {
  const w = 240;
  const path = useMemo(() => {
    const nums = values.filter((v): v is number => v !== null);
    if (nums.length < 2) return "";
    const max = Math.max(...nums) * 1.1;
    const min = Math.min(...nums) * 0.9;
    const span = Math.max(1, max - min);
    const step = w / Math.max(1, values.length - 1);
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      const x = i * step;
      const y = height - 2 - ((v - min) / span) * (height - 4);
      d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      pen = true;
    });
    return d;
  }, [values, height]);
  const color = state === "operational" || state === "unknown" ? "var(--trace)" : "var(--st)";
  return (
    <svg className={`st-${state} block w-full`} viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" height={height} aria-hidden>
      <line x1="0" x2={w} y1={height - 1} y2={height - 1} stroke="var(--line)" strokeWidth="1" />
      {path && <path d={path} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />}
    </svg>
  );
}

function dayState(d: UptimeDay): State {
  if (d.pct === null) return d.incident ? "degraded" : "unknown";
  // A few failed checks a day is normal network noise; colour only real problems.
  if (d.pct >= 99.5 && !d.incident) return "operational";
  if (d.pct >= 98) return "degraded";
  if (d.pct >= 90) return "partial_outage";
  return "major_outage";
}

/** 90 daily bars: check success rate, with provider-incident days marked. */
export function UptimeBar({ days, height = 22 }: { days: UptimeDay[]; height?: number }) {
  const [hover, setHover] = useState<UptimeDay | null>(null);
  return (
    <div className="relative">
      <div className="flex items-end gap-px" style={{ height }} onMouseLeave={() => setHover(null)}>
        {days.map((d) => {
          const st = dayState(d);
          return (
            <div
              key={d.day}
              className={`st-${st} flex-1`}
              onMouseEnter={() => setHover(d)}
              title={`${d.day}: ${d.pct === null ? "no data" : pct(d.pct)}${d.incident ? ", provider incident" : ""}`}
              style={{
                height: st === "unknown" ? "45%" : "100%",
                background: st === "unknown" ? "var(--line)" : "var(--st)",
                opacity: st === "operational" ? 0.55 : 1,
                borderRadius: 1,
              }}
            />
          );
        })}
      </div>
      {hover && (
        <div className="narrow pointer-events-none absolute -top-6 left-0 text-[0.75rem]" style={{ color: "var(--muted)" }}>
          {hover.day}: {hover.pct === null ? "no data" : `${pct(hover.pct)} of checks passed`}
          {hover.incident ? ", provider incident" : ""}
        </div>
      )}
    </div>
  );
}

const PHASES = [
  { key: "dns_ms", label: "DNS", color: "#7c6cf2" },
  { key: "connect_ms", label: "Connect", color: "#3fb7c9" },
  { key: "tls_ms", label: "TLS", color: "#e0a63a" },
  { key: "ttfb_ms", label: "Server", color: "#5ba7f5" },
] as const;

/** Latency over time. With timing breakdown (24h raw) it stacks DNS/connect/TLS/server time. */
const MAX_POINTS = 240;

/** Average neighbouring points so a day of 1-minute checks reads as a trend, keeping failures. */
function downsample(points: LatencyPoint[]): LatencyPoint[] {
  if (points.length <= MAX_POINTS) return points;
  const size = Math.ceil(points.length / MAX_POINTS);
  const out: LatencyPoint[] = [];
  for (let i = 0; i < points.length; i += size) {
    const chunk = points.slice(i, i + size);
    const ok = chunk.filter((p) => p.ok);
    const avg = (k: keyof LatencyPoint) => {
      const vals = ok.map((p) => p[k]).filter((v): v is number => typeof v === "number");
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    };
    const base = chunk[Math.floor(chunk.length / 2)];
    out.push({
      ...base,
      ok: ok.length === chunk.length,
      latency_ms: avg("latency_ms"),
      dns_ms: avg("dns_ms"),
      connect_ms: avg("connect_ms"),
      tls_ms: avg("tls_ms"),
      ttfb_ms: avg("ttfb_ms"),
      p95_ms: avg("p95_ms"),
    });
  }
  return out;
}

export function LatencyChart({ points: raw, height = 180 }: { points: LatencyPoint[]; height?: number }) {
  const w = 800;
  const points = useMemo(() => downsample(raw), [raw]);
  const stacked = points.some((p) => p.ttfb_ms !== undefined && p.ttfb_ms !== null);
  const { areas, line, band, max, fails, t0, t1 } = useMemo(() => {
    const ts = points.map((p) => new Date(p.ts).getTime());
    const t0 = Math.min(...ts);
    const t1 = Math.max(...ts);
    const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * w;
    const vals = points.map((p) => (stacked ? PHASES.reduce((a, ph) => a + (p[ph.key] ?? 0), 0) : Math.max(p.p95_ms ?? 0, p.latency_ms ?? 0)));
    const max = Math.max(10, ...vals.filter((v) => Number.isFinite(v))) * 1.1;
    const y = (v: number) => height - (v / max) * height;
    const fails = points.filter((p) => !p.ok).map((p) => x(new Date(p.ts).getTime()));
    let areas: { d: string; color: string; label: string }[] = [];
    let line = "";
    let band = "";
    if (stacked) {
      const okPts = points.filter((p) => p.ok);
      let base = okPts.map(() => 0);
      areas = PHASES.map((ph) => {
        const top = okPts.map((p, i) => base[i] + (p[ph.key] ?? 0));
        const upper = okPts.map((p, i) => `${x(new Date(p.ts).getTime()).toFixed(1)},${y(top[i]).toFixed(1)}`);
        const lower = okPts.map((p, i) => `${x(new Date(p.ts).getTime()).toFixed(1)},${y(base[i]).toFixed(1)}`).reverse();
        base = top;
        return { d: okPts.length ? `M${upper.join("L")}L${lower.join("L")}Z` : "", color: ph.color, label: ph.label };
      });
    } else {
      const okPts = points.filter((p) => p.latency_ms !== null);
      line = okPts.map((p, i) => `${i ? "L" : "M"}${x(new Date(p.ts).getTime()).toFixed(1)},${y(p.latency_ms ?? 0).toFixed(1)}`).join("");
      const hi = okPts.filter((p) => p.p95_ms != null);
      if (hi.length) {
        band =
          "M" +
          hi.map((p) => `${x(new Date(p.ts).getTime()).toFixed(1)},${y(p.p95_ms ?? 0).toFixed(1)}`).join("L") +
          "L" +
          [...hi].reverse().map((p) => `${x(new Date(p.ts).getTime()).toFixed(1)},${y(p.latency_ms ?? 0).toFixed(1)}`).join("L") +
          "Z";
      }
    }
    return { areas, line, band, max, fails, t0, t1 };
  }, [points, stacked, height]);

  if (points.length < 2) return <p className="text-sm" style={{ color: "var(--muted)" }}>Not enough data yet.</p>;
  return (
    <figure className="m-0">
      <div className="relative">
        <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="block w-full" height={height} role="img" aria-label="Latency chart">
          {[0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1="0" x2={w} y1={height * f} y2={height * f} stroke="var(--line)" strokeDasharray="2 4" />
          ))}
          {fails.map((fx, i) => (
            <rect key={i} x={fx - 1} y={0} width={2} height={height} fill="var(--major)" opacity={0.35} />
          ))}
          {areas.map((a) => a.d && <path key={a.label} d={a.d} fill={a.color} opacity={0.75} />)}
          {band && <path d={band} fill="var(--trace)" opacity={0.18} />}
          {line && <path d={line} fill="none" stroke="var(--trace)" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />}
        </svg>
        <span className="narrow absolute left-1 top-0 text-[0.75rem]" style={{ color: "var(--muted)" }}>
          {ms(max)}
        </span>
      </div>
      <figcaption className="narrow mt-1 flex flex-wrap justify-between gap-2 text-[0.75rem]" style={{ color: "var(--muted)" }}>
        <span>{new Date(t0).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
        <span className="flex flex-wrap gap-3">
          {stacked
            ? PHASES.map((p) => (
                <span key={p.key} className="inline-flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-sm" style={{ background: p.color }} />
                  {p.label}
                </span>
              ))
            : <span>Median with p95 band</span>}
          {fails.length > 0 && (
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-0.5" style={{ background: "var(--major)" }} />
              Failed check
            </span>
          )}
        </span>
        <span>{new Date(t1).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
      </figcaption>
    </figure>
  );
}
