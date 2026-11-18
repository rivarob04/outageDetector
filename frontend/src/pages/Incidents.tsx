import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi, usePulse } from "../api/store";
import type { IncidentItem, ProviderStats } from "../api/types";
import { duration } from "../lib/format";
import { IncidentCard } from "./ServiceDetail";

const STATUSES = [
  ["", "All"],
  ["active", "Ongoing"],
  ["upcoming", "Upcoming maintenance"],
  ["resolved", "Resolved"],
] as const;

export default function Incidents() {
  const [params, setParams] = useSearchParams();
  const { summary } = usePulse();
  const status = params.get("status") ?? "";
  const service = params.get("service") ?? "";
  const kind = params.get("kind") ?? "";
  const [days, setDays] = useState(90);
  const [page, setPage] = useState(0);
  const qs = new URLSearchParams({ days: String(days), limit: "30", offset: String(page * 30) });
  if (status) qs.set("status", status);
  if (service) qs.set("service", service);
  if (kind) qs.set("kind", kind);
  const { data } = useApi<{ total: number; items: IncidentItem[] }>(`/api/v1/incidents?${qs}`);
  const { data: stats } = useApi<{ providers: ProviderStats[] }>(`/api/v1/incidents/stats?days=${days}`);

  function set(key: string, value: string) {
    const p = new URLSearchParams(params);
    if (value) p.set(key, value);
    else p.delete(key);
    setParams(p);
    setPage(0);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
      <div>
        <h1 className="wide mb-3 mt-0 text-3xl font-extrabold">Incidents</h1>
        <div className="mb-4 flex flex-wrap gap-2">
          <div role="group" aria-label="Status" className="flex flex-wrap gap-1.5">
            {STATUSES.map(([v, l]) => (
              <button key={v} className="chip" aria-pressed={status === v} onClick={() => set("status", v)}>
                {l}
              </button>
            ))}
          </div>
          <select className="field w-auto" aria-label="Type" value={kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="">Incidents and maintenance</option>
            <option value="incident">Incidents only</option>
            <option value="maintenance">Maintenance only</option>
          </select>
          <select className="field w-auto" aria-label="Service" value={service} onChange={(e) => set("service", e.target.value)}>
            <option value="">All watched services</option>
            {summary?.services.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <select className="field w-auto" aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 30, 90, 365].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </select>
        </div>
        {!data ? (
          <p style={{ color: "var(--muted)" }}>Loading…</p>
        ) : data.items.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>No incidents match. Provider incidents appear here as soon as a status feed reports them.</p>
        ) : (
          <>
            <ul className="m-0 grid gap-1 p-0">
              {data.items.map((i) => (
                <IncidentCard key={i.id} inc={i} showService />
              ))}
            </ul>
            <div className="mt-4 flex items-center gap-3 text-sm">
              <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>
                Newer
              </button>
              <span style={{ color: "var(--muted)" }}>
                {page * 30 + 1} to {Math.min(data.total, (page + 1) * 30)} of {data.total}
              </span>
              <button className="btn" disabled={(page + 1) * 30 >= data.total} onClick={() => setPage(page + 1)}>
                Older
              </button>
            </div>
          </>
        )}
      </div>
      <aside>
        <section className="panel rounded-lg p-4">
          <h2 className="mb-1 mt-0 text-base font-bold">Time to recover</h2>
          <p className="mb-3 mt-0 text-sm" style={{ color: "var(--muted)" }}>
            Mean time from the start of a provider incident to its resolution. Lead time is how long after Pulse saw widespread failures the provider posted an incident.
          </p>
          {!stats?.providers.length ? (
            <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
              No resolved incidents yet.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="narrow text-left" style={{ color: "var(--muted)" }}>
                  <th className="pb-1 font-medium">Provider</th>
                  <th className="pb-1 text-right font-medium">Incidents</th>
                  <th className="pb-1 text-right font-medium">Mean</th>
                  <th className="pb-1 text-right font-medium" title="How long after Pulse saw widespread failures the provider posted an incident">
                    Lead time
                  </th>
                </tr>
              </thead>
              <tbody>
                {stats.providers.map((p) => (
                  <tr key={p.service_id} className="border-t" style={{ borderColor: "var(--line)" }}>
                    <td className="py-1.5">
                      <Link to={`/service/${p.service_id}`}>{p.service_name}</Link>
                    </td>
                    <td className="py-1.5 text-right">{p.count}</td>
                    <td className="py-1.5 text-right">{p.mttr_s != null ? duration(p.mttr_s * 1000) : "–"}</td>
                    <td className="py-1.5 text-right" title={p.lead ? `${p.lead.confirmed} confirmed by the provider, ${p.lead.unconfirmed} never confirmed` : undefined}>
                      {p.lead?.median_lead_s != null ? duration(p.lead.median_lead_s * 1000) : p.lead?.unconfirmed ? `${p.lead.unconfirmed} unconfirmed` : "–"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </aside>
    </div>
  );
}
