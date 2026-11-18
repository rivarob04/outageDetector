import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi, usePulse } from "../api/store";
import type { IncidentItem, LatencySeries, ServiceDetail as Detail } from "../api/types";
import { LatencyChart, UptimeBar } from "../components/Charts";
import { Glyph, Monogram, ScopeBadge, StateLabel } from "../components/Status";
import { dateTime, duration, ms, pct, timeAgo } from "../lib/format";
import { STATE_LABEL, componentState, impactToState, isProblem } from "../lib/status";

const RANGES = ["24h", "7d", "30d", "90d"] as const;

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="panel rounded-lg p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h2 className="m-0 text-base font-bold">{title}</h2>
        <div className="ml-auto">{aside}</div>
      </div>
      {children}
    </section>
  );
}

export function IncidentCard({ inc, showService = false }: { inc: IncidentItem; showService?: boolean }) {
  const [open, setOpen] = useState(false);
  const st = inc.kind === "maintenance" ? "maintenance" : inc.resolved_at ? "operational" : impactToState(inc.impact);
  const updates = inc.updates ?? [];
  return (
    <li className={`st-${st} list-none border-l-2 py-2 pl-4`} style={{ borderColor: "var(--st)" }}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Glyph state={st} size={10} />
        {showService && (
          <Link to={`/service/${inc.service_id}`} className="font-semibold">
            {inc.service_name}
          </Link>
        )}
        <span className="font-medium">{inc.title}</span>
        <span className="narrow text-[0.8125rem]" style={{ color: "var(--muted)" }}>
          {inc.kind === "maintenance" && inc.scheduled_for
            ? `${dateTime(inc.scheduled_for)} to ${dateTime(inc.scheduled_until)}`
            : `${dateTime(inc.started_at)}${inc.resolved_at ? `, resolved after ${duration((inc.duration_s ?? 0) * 1000)}` : ", ongoing"}`}
        </span>
      </div>
      {updates.length > 0 && (
        <button className="mt-1 bg-transparent p-0 text-[0.8125rem] underline" style={{ color: "var(--muted)", border: 0, cursor: "pointer" }} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide updates" : `Show ${updates.length} update${updates.length === 1 ? "" : "s"}`}
        </button>
      )}
      {open && (
        <ol className="prose-updates mt-2 grid gap-2 p-0 text-sm">
          {updates.map((u, i) => (
            <li key={i} className="list-none">
              <span className="narrow font-semibold capitalize">{u.status || "update"}</span>{" "}
              <span className="narrow text-[0.8125rem]" style={{ color: "var(--muted)" }}>
                {dateTime(u.created_at)}
              </span>
              <p className="max-w-[78ch]">{u.body}</p>
            </li>
          ))}
        </ol>
      )}
      {inc.url && (
        <a href={inc.url} target="_blank" rel="noreferrer" className="ml-3 text-[0.8125rem]">
          Open on status page
        </a>
      )}
    </li>
  );
}

export default function ServiceDetail() {
  const { id = "" } = useParams();
  const { isAdmin, summary, refresh } = usePulse();
  const navigate = useNavigate();
  const [range, setRange] = useState<(typeof RANGES)[number]>("24h");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const tileState = summary?.services.find((s) => s.id === id)?.state;
  const { data: d, error, reload } = useApi<Detail>(`/api/v1/services/${id}`, [tileState]);
  const { data: lat } = useApi<LatencySeries>(d ? `/api/v1/services/${id}/latency?range=${range}` : null, [tileState]);
  const [showAllComps, setShowAllComps] = useState(false);

  if (error) return <p role="alert">{error === "unknown service" ? "This service doesn't exist." : error}</p>;
  if (!d) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg(done);
      reload();
      await refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const problemComps = d.components.filter((c) => c.status !== "operational");
  const comps = showAllComps ? d.components : problemComps.slice(0, 40);
  const why = isProblem(d.state) ? `Why it's marked ${STATE_LABEL[d.state].toLowerCase()}` : "How Pulse decided";

  return (
    <div className={`st-${d.state} grid gap-4`}>
      <nav className="text-sm">
        <Link to="/">Status</Link> <span style={{ color: "var(--faint)" }}>/</span> {d.name}
      </nav>
      <header className="flex flex-wrap items-start gap-4">
        <Monogram id={d.id} name={d.name} size={52} />
        <div className="min-w-0 flex-1">
          <h1 className="wide m-0 text-[clamp(1.6rem,3.5vw,2.4rem)] font-extrabold leading-tight">{d.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <StateLabel state={d.state} />
            <ScopeBadge scope={d.scope} state={d.state} />
            {d.since && (
              <span className="narrow text-sm" style={{ color: "var(--muted)" }}>
                since {dateTime(d.since)}
              </span>
            )}
            <span className="narrow text-sm" style={{ color: "var(--muted)" }}>
              checked {timeAgo(d.last_checked)}
            </span>
          </div>
          <p className="narrow mt-2 mb-0 flex flex-wrap gap-x-4 text-sm">
            {d.status_page && (
              <a href={d.status_page} target="_blank" rel="noreferrer">
                Official status page
              </a>
            )}
            {d.homepage && (
              <a href={d.homepage} target="_blank" rel="noreferrer">
                Website
              </a>
            )}
            <span style={{ color: "var(--muted)" }}>
              {d.signal === "feed+checks" ? "Status feed and checks" : d.signal === "feed" ? "Status feed only" : "Checks only"}
              {d.source !== "catalog" ? ", your own service" : ""}
            </span>
            {d.depends_on.length > 0 && (
              <span style={{ color: "var(--muted)" }} title="Pulse adds an advisory when a watched upstream has problems">
                Depends on {d.depends_on.map((dep) => dep.replace(/^aws:/, "AWS ").replace(/^gcp:/, "Google Cloud ")).join(", ")}
              </span>
            )}
          </p>
        </div>
        {isAdmin && (
          <div className="flex flex-wrap gap-2">
            {d.tracked && (
              <button className="btn" disabled={busy} onClick={() => act(() => api(`/api/v1/services/${id}/check`, { method: "POST" }), "Checking now.")}>
                Check now
              </button>
            )}
            {d.source === "custom" && (
              <Link className="btn no-underline" to={`/catalog?edit=${id}`}>
                Edit
              </Link>
            )}
            {d.source === "catalog" ? (
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  act(() => api(`/api/v1/services/${id}/track`, { method: d.tracked ? "DELETE" : "POST" }), d.tracked ? "Stopped watching." : "Watching.")
                }
              >
                {d.tracked ? "Stop watching" : "Watch"}
              </button>
            ) : d.source === "custom" ? (
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(`Delete ${d.name} and its history?`))
                    void act(() => api(`/api/v1/services/${id}`, { method: "DELETE" }), "Deleted.").then(() => navigate("/"));
                }}
              >
                Delete
              </button>
            ) : null}
          </div>
        )}
      </header>
      {msg && (
        <p role="status" className="m-0 text-sm" style={{ color: "var(--muted)" }}>
          {msg}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-4">
          <Section title={why}>
            <ul className="m-0 grid gap-1.5 pl-5">
              {d.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            {d.advisories.length > 0 && (
              <ul className="mb-0 mt-3 grid gap-1 pl-5" style={{ color: "var(--degraded)" }}>
                {d.advisories.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            )}
            {d.notes && (
              <p className="mb-0 mt-3 text-sm" style={{ color: "var(--muted)" }}>
                {d.notes}
              </p>
            )}
          </Section>

          <Section
            title="Response time"
            aside={
              <div role="group" aria-label="Time range" className="flex gap-1">
                {RANGES.map((r) => (
                  <button key={r} className="chip" aria-pressed={range === r} onClick={() => setRange(r)}>
                    {r}
                  </button>
                ))}
              </div>
            }
          >
            {d.probes.length === 0 ? (
              <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
                This service is followed through its status feed only, so there's no response time to chart.
              </p>
            ) : (
              lat?.probes
                .filter((p) => p.kind === "http" || lat.probes.length === 1)
                .slice(0, 2)
                .map((p) => (
                  <div key={p.id} className="mb-4 last:mb-0">
                    <p className="narrow mb-1 mt-0 truncate text-[0.8125rem]" style={{ color: "var(--muted)" }}>
                      {p.target}
                    </p>
                    <LatencyChart points={p.points} />
                  </div>
                ))
            )}
          </Section>

          <Section title="Incidents and maintenance">
            {d.incidents.length === 0 ? (
              <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
                No incidents recorded{d.feed ? "" : "; this service has no official status feed"}.
              </p>
            ) : (
              <ul className="m-0 grid gap-1 p-0">
                {d.incidents.map((i) => (
                  <IncidentCard key={i.id} inc={i} />
                ))}
              </ul>
            )}
          </Section>
        </div>

        <div className="grid content-start gap-4">
          <Section title="90 days" aside={<span className="narrow text-sm">{pct(d.uptime_pct, 3)} of checks passed</span>}>
            <UptimeBar days={d.uptime} height={30} />
          </Section>

          <Section title="Checks from your network">
            {d.probes.length === 0 ? (
              <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
                No checks configured.
              </p>
            ) : (
              <ul className="m-0 grid gap-3 p-0">
                {d.probes.map((p) => {
                  const l = p.latest;
                  const st = !l ? "unknown" : l.ok ? "operational" : "major_outage";
                  const days = l?.detail && typeof l.detail.days_left === "number" ? (l.detail.days_left as number) : null;
                  return (
                    <li key={p.id} className="list-none">
                      <div className="flex items-center gap-2">
                        <Glyph state={st} size={10} />
                        <span className="narrow font-semibold uppercase">{p.kind}</span>
                        <span className="min-w-0 flex-1 truncate text-sm">{p.url ?? `${p.host}${p.port ? `:${p.port}` : ""}`}</span>
                      </div>
                      <p className="narrow mb-0 mt-0.5 pl-5 text-[0.8125rem]" style={{ color: "var(--muted)" }}>
                        {!l
                          ? "Not checked yet"
                          : l.ok
                            ? [
                                `${ms(l.latency_ms)}`,
                                l.status_code && `HTTP ${l.status_code}`,
                                l.dns_ms != null && `DNS ${ms(l.dns_ms)}`,
                                l.connect_ms != null && `connect ${ms(l.connect_ms)}`,
                                l.tls_ms != null && `TLS ${ms(l.tls_ms)}`,
                                days !== null && `certificate valid ${Math.floor(days)} more days`,
                              ]
                                .filter(Boolean)
                                .join(", ")
                            : `Failed: ${l.error}${l.status_code ? ` (HTTP ${l.status_code})` : ""}${l.detail && l.detail.message ? `, ${String(l.detail.message)}` : ""}`}
                        {l && `, ${timeAgo(l.ts)}`}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>

          {d.agents.length > 0 && (
            <Section title="From other places">
              <table className="w-full text-sm">
                <tbody>
                  {d.agents.map((a) => {
                    const failing = a.results.some((r) => r.consecutive_failures >= 2);
                    const latencies = a.results.map((r) => r.latency_ms).filter((v): v is number => v !== null);
                    return (
                      <tr key={a.id} className={failing ? "st-major_outage" : "st-operational"}>
                        <td className="w-5 py-1">
                          <Glyph state={failing ? "major_outage" : "operational"} size={10} />
                        </td>
                        <td className="py-1">{a.name}</td>
                        <td className="narrow py-1 text-right" style={{ color: "var(--muted)" }}>
                          {failing ? "failing" : latencies.length ? ms(Math.min(...latencies)) : "ok"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Section>
          )}

          {d.feed && (
            <Section title="Official status feed">
              <p className="narrow m-0 text-sm" style={{ color: "var(--muted)" }}>
                {d.feed.ok === false ? `Last fetch failed: ${d.feed.error}` : d.feed.description || "Fetched successfully"}. Last fetched {timeAgo(d.feed.fetched_at)}.
              </p>
              {d.components.length > 0 && (
                <>
                  <p className="mb-1 mt-3 text-sm">
                    {problemComps.length
                      ? `${problemComps.length} of ${d.components.length} components affected`
                      : `All ${d.components.length} components operational`}
                  </p>
                  <ul className="m-0 grid max-h-72 gap-1 overflow-auto p-0">
                    {comps.map((c) => (
                      <li key={`${c.group}/${c.name}`} className="flex list-none items-center gap-2 text-sm">
                        <Glyph state={componentState(c.status)} size={9} />
                        <span className="truncate">
                          {c.group ? <span style={{ color: "var(--muted)" }}>{c.group} / </span> : null}
                          {c.name}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {d.components.length > problemComps.length && (
                    <button className="chip mt-2" onClick={() => setShowAllComps(!showAllComps)}>
                      {showAllComps ? "Show affected only" : "Show all components"}
                    </button>
                  )}
                </>
              )}
            </Section>
          )}

          {d.transitions.length > 0 && (
            <Section title="Recent changes">
              <ol className="m-0 grid gap-1.5 p-0">
                {d.transitions.slice(0, 10).map((t, i) => (
                  <li key={i} className="flex list-none items-center gap-2 text-sm">
                    <Glyph state={t.to_state} size={9} />
                    <span>{STATE_LABEL[t.to_state]}</span>
                    <span className="narrow ml-auto" style={{ color: "var(--muted)" }}>
                      {dateTime(t.ts)}
                    </span>
                  </li>
                ))}
              </ol>
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}
