import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { usePulse } from "../api/store";
import type { ServiceSummary, Summary } from "../api/types";
import { Sparkline, UptimeBar } from "../components/Charts";
import { Glyph, Monogram, ScopeBadge } from "../components/Status";
import { duration, pct, timeAgo } from "../lib/format";
import { setPrefs, usePrefs } from "../lib/prefs";
import { STATE_LABEL, STATE_RANK, isProblem } from "../lib/status";

function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The one-sentence answer to "is it down, or is it me?". */
export function verdictSentence(s: Summary): { headline: string; detail: string; tone: string } {
  if (s.connectivity.offline) {
    return {
      headline: "You're offline.",
      detail: "This host can't reach the internet, so checks are paused. Services marked Unknown will update once you're back.",
      tone: "unknown",
    };
  }
  const problems = s.services.filter((x) => isProblem(x.state)).sort((a, b) => STATE_RANK[b.state] - STATE_RANK[a.state]);
  const maint = s.services.filter((x) => x.state === "maintenance");
  if (problems.length === 0) {
    const m = maint.length ? ` ${joinNames(maint.map((x) => x.name))} ${maint.length === 1 ? "is" : "are"} in scheduled maintenance.` : "";
    return {
      headline: s.services.length ? "Everything's up." : "Nothing watched yet.",
      detail: s.services.length
        ? `All ${s.services.length} services you watch look healthy from here.${m}`
        : "Add services from the catalog, or your own URLs, to start watching them.",
      tone: s.services.length ? "operational" : "unknown",
    };
  }
  const tone = problems[0].state;
  const confirmed = problems.filter((p) => p.scope === "provider_confirmed").length;
  const wide = problems.filter((p) => p.scope === "widespread").length;
  const local = problems.filter((p) => p.scope === "local_only").length;
  const parts = [
    confirmed && `${confirmed} confirmed by the provider`,
    wide && `${wide} failing from several places without confirmation yet`,
    local && `${local} failing only from your network`,
  ].filter(Boolean) as string[];
  let headline: string;
  if (problems.length === 1) {
    const p = problems[0];
    const what = p.state === "degraded" ? "degraded" : p.state === "partial_outage" ? "partly down" : "down";
    headline =
      p.scope === "local_only"
        ? `${p.name} is ${what}, but only from here.`
        : p.scope === "widespread"
          ? `${p.name} looks ${what} for everyone.`
          : `${p.name} is ${what}.`;
  } else if (problems.length <= 3) {
    headline = `${joinNames(problems.map((p) => p.name))} are having problems.`;
  } else {
    headline = `${problems.length} services are having problems.`;
  }
  return { headline, detail: parts.length ? `${parts.join(", ")}.`.replace(/^./, (c) => c.toUpperCase()) : "", tone };
}

function Hero({ s }: { s: Summary }) {
  const v = verdictSentence(s);
  const canaries = s.connectivity.canaries;
  const okCan = canaries.filter((c) => c.ok);
  const median = okCan.length ? okCan.map((c) => c.latency_ms ?? 0).sort((a, b) => a - b)[Math.floor(okCan.length / 2)] : null;
  return (
    <section aria-labelledby="verdict" className={`st-${v.tone} mb-8`}>
      <div className="flex items-start gap-4">
        <span className="mt-3 hidden sm:block">
          <Glyph state={v.tone as ServiceSummary["state"]} size={22} />
        </span>
        <div className="min-w-0">
          <h1 id="verdict" className="wide m-0 text-[clamp(1.9rem,4.6vw,3.4rem)] font-extrabold leading-[1.05]" style={{ color: v.tone === "operational" ? "var(--text)" : "var(--st)" }}>
            {v.headline}
          </h1>
          {v.detail && <p className="mt-3 mb-0 max-w-[68ch] text-[1.05rem]" style={{ color: "var(--muted)" }}>{v.detail}</p>}
          <p className="narrow mt-3 mb-0 flex flex-wrap gap-x-5 gap-y-1 text-sm" style={{ color: "var(--muted)" }}>
            <span className={s.connectivity.offline ? "st-major_outage" : "st-operational"}>
              <span className="inline-flex items-center gap-1.5">
                <Glyph state={s.connectivity.offline ? "major_outage" : "operational"} size={9} />
                Your connection {s.connectivity.offline ? `is down for ${duration(Date.now() - new Date(s.connectivity.offline_since ?? Date.now()).getTime())}` : "is fine"}
                {median !== null && !s.connectivity.offline ? ` (${Math.round(median)} ms to ${okCan.length} test sites)` : ""}
              </span>
            </span>
            <span>Updated {timeAgo(s.generated_at)}</span>
          </p>
        </div>
      </div>
    </section>
  );
}

function Banners({ s }: { s: Summary }) {
  const items = s.services.flatMap((svc) => svc.incidents.filter((i) => i.kind === "incident").map((i) => ({ svc, i })));
  if (!items.length) return null;
  return (
    <section aria-label="Active provider incidents" className="mb-6 grid gap-2">
      {items.slice(0, 5).map(({ svc, i }) => (
        <div key={i.id} className={`st-${svc.state} panel flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-4 py-2.5`} style={{ borderLeft: "4px solid var(--st)" }}>
          <Glyph state={svc.state} />
          <Link to={`/service/${svc.id}`} className="font-semibold no-underline hover:underline">
            {svc.name}
          </Link>
          <span className="w-full min-w-0 sm:w-auto sm:flex-1">{i.title}</span>
          <span className="narrow text-sm" style={{ color: "var(--muted)" }}>
            started {timeAgo(i.started_at)}
          </span>
          {i.url && (
            <a href={i.url} target="_blank" rel="noreferrer" className="text-sm">
              Provider update
            </a>
          )}
        </div>
      ))}
      {items.length > 5 && (
        <Link to="/incidents?status=active" className="text-sm">
          Show all {items.length} active incidents
        </Link>
      )}
    </section>
  );
}

function Tile({ svc, big, compact, flash }: { svc: ServiceSummary; big: boolean; compact: boolean; flash: boolean }) {
  const inc = svc.incidents[0];
  return (
    <Link
      to={`/service/${svc.id}`}
      className={`st-${svc.state} panel group flex min-w-0 flex-col rounded-lg no-underline ${compact ? "gap-1.5 p-3" : "gap-2.5 p-4"} ${big ? "sm:col-span-2" : ""} ${flash ? "flash" : ""}`}
      style={isProblem(svc.state) ? { borderColor: "color-mix(in srgb, var(--st) 55%, var(--line))" } : undefined}
      aria-label={`${svc.name}: ${STATE_LABEL[svc.state]}`}
    >
      <div className="flex items-start gap-3">
        <Monogram id={svc.id} name={svc.name} size={compact ? 26 : 32} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold">{svc.name}</span>
          </div>
          <div className="narrow flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.8125rem]">
            <span className="inline-flex items-center gap-1.5" style={{ color: svc.state === "operational" ? "var(--muted)" : "var(--st)" }}>
              <Glyph state={svc.state} size={10} />
              <span className="font-semibold">{STATE_LABEL[svc.state]}</span>
            </span>
            {svc.since && svc.state !== "operational" && svc.state !== "unknown" && (
              <span style={{ color: "var(--muted)" }}>for {duration(Date.now() - new Date(svc.since).getTime())}</span>
            )}
            <ScopeBadge scope={svc.scope} state={svc.state} />
          </div>
        </div>
        {svc.uptime_pct !== null && !compact && (
          <span className="narrow text-[0.8125rem]" style={{ color: "var(--muted)" }} title="Share of checks that passed over 90 days">
            {pct(svc.uptime_pct)}
          </span>
        )}
      </div>
      {big && (
        <p className="m-0 text-sm" style={{ color: "var(--text)" }}>
          {inc ? inc.title : svc.reasons[0]}
        </p>
      )}
      {svc.advisories.length > 0 && (
        <p className="narrow m-0 text-[0.8125rem]" style={{ color: "var(--degraded)" }}>
          {svc.advisories[0]}
        </p>
      )}
      {svc.sparkline.some((v) => v !== null) ? (
        <Sparkline values={svc.sparkline} state={svc.state} height={compact ? 22 : 34} />
      ) : (
        <p className="narrow m-0 text-[0.8125rem]" style={{ color: "var(--faint)" }}>
          {svc.signal === "feed" ? "Follows the official status feed" : "Waiting for checks"}
        </p>
      )}
      {!compact && <UptimeBar days={svc.uptime} height={14} />}
    </Link>
  );
}

export default function Dashboard({ kiosk = false }: { kiosk?: boolean }) {
  const { summary, error, changed } = usePulse();
  const prefs = usePrefs();
  const [cat, setCat] = useState<string>("all");
  const [q, setQ] = useState("");

  const services = useMemo(() => {
    if (!summary) return [];
    let list = summary.services;
    if (cat !== "all") list = list.filter((s) => (cat === "problems" ? isProblem(s.state) : s.category === cat));
    if (q.trim()) {
      const n = q.trim().toLowerCase();
      list = list.filter((s) => s.name.toLowerCase().includes(n) || s.id.includes(n));
    }
    const byName = (a: ServiceSummary, b: ServiceSummary) => a.name.localeCompare(b.name);
    if (prefs.sort === "name") return [...list].sort(byName);
    if (prefs.sort === "category") return [...list].sort((a, b) => a.category_label.localeCompare(b.category_label) || byName(a, b));
    return [...list].sort((a, b) => STATE_RANK[b.state] - STATE_RANK[a.state] || byName(a, b));
  }, [summary, cat, q, prefs.sort]);

  if (error && !summary) {
    return (
      <p role="alert" className="text-lg">
        Pulse can't load status right now: {error}
      </p>
    );
  }
  if (!summary) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  const cats = Array.from(new Set(summary.services.map((s) => s.category)));
  const problems = summary.services.filter((s) => isProblem(s.state)).length;
  const compact = prefs.density === "compact" || kiosk;

  return (
    <>
      <Hero s={summary} />
      {!kiosk && <Banners s={summary} />}
      {!kiosk && summary.services.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-1.5">
            <button className="chip" aria-pressed={cat === "all"} onClick={() => setCat("all")}>
              All {summary.services.length}
            </button>
            {problems > 0 && (
              <button className="chip" aria-pressed={cat === "problems"} onClick={() => setCat("problems")}>
                Problems {problems}
              </button>
            )}
            {cats.map((c) => (
              <button key={c} className="chip" aria-pressed={cat === c} onClick={() => setCat(c)}>
                {summary.categories[c] ?? c}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <label className="sr-only" htmlFor="search">
              Search services
            </label>
            <input id="search" className="field w-44" placeholder="Search  /" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="field w-auto" aria-label="Sort" value={prefs.sort} onChange={(e) => setPrefs({ sort: e.target.value as typeof prefs.sort })}>
              <option value="status">Problems first</option>
              <option value="name">A to Z</option>
              <option value="category">By category</option>
            </select>
            <button className="btn" aria-pressed={compact} onClick={() => setPrefs({ density: compact ? "comfortable" : "compact" })}>
              {compact ? "Comfortable" : "Compact"}
            </button>
          </div>
        </div>
      )}
      {summary.services.length === 0 ? (
        <div className="panel rounded-lg p-6">
          <p className="mt-0">Pick the services you rely on and Pulse starts checking them right away.</p>
          <Link to="/catalog" className="btn btn-primary no-underline">
            Add services
          </Link>
        </div>
      ) : (
        <div
          className={`grid gap-3 ${prefs.mobileCols === 2 ? "grid-cols-2" : "grid-cols-1"} sm:grid-cols-[repeat(auto-fill,minmax(17rem,1fr))]`}
        >
          {services.map((svc) => (
            <Tile key={svc.id} svc={svc} big={isProblem(svc.state) && !compact} compact={compact} flash={changed.has(svc.id)} />
          ))}
          {services.length === 0 && <p style={{ color: "var(--muted)" }}>No services match this filter.</p>}
        </div>
      )}
    </>
  );
}
