import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApi } from "../api/store";
import { timeAgo } from "../lib/format";
import { Card } from "../pages/Settings";
import { Glyph } from "./Status";

interface AgentRow {
  id: string;
  name: string;
  location: string | null;
  lat: number | null;
  lon: number | null;
  last_seen_at: string | null;
  version: string | null;
  revoked: boolean;
  fresh: boolean;
}

export default function AgentsSettings() {
  const { data, reload } = useApi<AgentRow[]>("/api/v1/agents");
  const [created, setCreated] = useState<{ token: string; hub_url: string; name: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const num = (k: string) => (f.get(k) ? Number(f.get(k)) : null);
    try {
      const res = await api<{ token: string; hub_url: string }>("/api/v1/agents", {
        method: "POST",
        json: { name: f.get("name"), location: f.get("location") || null, lat: num("lat"), lon: num("lon") },
      });
      setCreated({ ...res, name: String(f.get("name")) });
      e.currentTarget.reset();
      reload();
    } catch (ex) {
      setErr((ex as Error).message);
    }
  }

  async function act(path: string, method: string, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    await api(path, { method });
    reload();
  }

  const compose = created
    ? `services:
  pulse-agent:
    image: ghcr.io/rivarob04/outagedetector:latest
    command: ["agent"]
    restart: unless-stopped
    environment:
      PULSE_HUB_URL: ${created.hub_url}
      PULSE_AGENT_TOKEN: ${created.token}
    read_only: true
    cap_drop: [ALL]`
    : "";

  return (
    <Card
      title="Vantage agents"
      intro="An agent runs the same checks from another network (a cheap VPS, a friend's Raspberry Pi) and reports back over HTTPS. It never accepts incoming connections. With agents, Pulse can tell a real outage from a problem on your own connection."
    >
      {data && data.length > 0 && (
        <table className="mb-4 w-full text-sm">
          <thead>
            <tr className="narrow text-left" style={{ color: "var(--muted)" }}>
              <th className="pb-1 font-medium">Agent</th>
              <th className="pb-1 font-medium">Last report</th>
              <th className="pb-1 font-medium">Version</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((a) => (
              <tr key={a.id} className="border-t" style={{ borderColor: "var(--line)" }}>
                <td className="py-2">
                  <span className="inline-flex items-center gap-2">
                    <Glyph state={a.revoked ? "unknown" : a.fresh ? "operational" : "degraded"} size={10} />
                    {a.name}
                    {a.location && <span style={{ color: "var(--muted)" }}>{a.location}</span>}
                  </span>
                </td>
                <td className="py-2">{a.revoked ? "revoked" : timeAgo(a.last_seen_at)}</td>
                <td className="narrow py-2">{a.version ?? "–"}</td>
                <td className="py-2 text-right">
                  {!a.revoked && (
                    <button className="btn" onClick={() => act(`/api/v1/agents/${a.id}/revoke`, "POST", `Revoke ${a.name}? Its token stops working immediately.`)}>
                      Revoke
                    </button>
                  )}{" "}
                  <button className="btn btn-danger" onClick={() => act(`/api/v1/agents/${a.id}`, "DELETE", `Delete ${a.name} and its results?`)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {created ? (
        <div className="grid gap-2">
          <p className="m-0 font-semibold">Agent {created.name} created. Copy its token now; it won't be shown again.</p>
          <pre className="m-0 overflow-auto rounded p-3 text-[0.8125rem]" style={{ background: "var(--bg)", border: "1px solid var(--line)" }}>
            {compose}
          </pre>
          <div className="flex gap-2">
            <button className="btn" onClick={() => navigator.clipboard?.writeText(compose)}>
              Copy compose file
            </button>
            <button className="btn" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={create} className="grid items-end gap-3 sm:grid-cols-[1.2fr_1.2fr_0.7fr_0.7fr_auto]">
          <label>
            <span className="label">Name</span>
            <input name="name" className="field" required placeholder="Frankfurt VPS" />
          </label>
          <label>
            <span className="label">Location</span>
            <input name="location" className="field" placeholder="Frankfurt, DE" />
          </label>
          <label>
            <span className="label">Latitude</span>
            <input name="lat" className="field" type="number" step="any" min={-90} max={90} />
          </label>
          <label>
            <span className="label">Longitude</span>
            <input name="lon" className="field" type="number" step="any" min={-180} max={180} />
          </label>
          <button className="btn btn-primary">Create agent</button>
          {err && (
            <p role="alert" className="m-0 text-sm sm:col-span-5" style={{ color: "var(--major)" }}>
              {err}
            </p>
          )}
        </form>
      )}
    </Card>
  );
}
