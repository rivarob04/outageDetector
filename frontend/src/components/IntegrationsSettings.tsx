import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApi, usePulse } from "../api/store";
import { Card } from "../pages/Settings";

interface PageCfg {
  enabled: boolean;
  title: string;
  intro: string;
  services: string[];
}

export function StatusPageSettings() {
  const { summary } = usePulse();
  const { data, reload } = useApi<PageCfg>("/api/v1/status-page");
  const [cfg, setCfg] = useState<PageCfg | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data) setCfg(data);
  }, [data]);
  if (!cfg) return null;

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/api/v1/status-page", { method: "PUT", json: cfg });
      setMsg("Saved.");
      reload();
    } catch (ex) {
      setMsg((ex as Error).message);
    }
  }

  return (
    <Card title="Public status page" intro="Share a simple page at /status with the services you pick, for family or your team. It shows no admin data, and problems that only affect your network are never shown there.">
      <form onSubmit={save} className="grid gap-3">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} />
          Publish the page at <a href="/status" target="_blank" rel="noreferrer">/status</a>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label>
            <span className="label">Title</span>
            <input className="field" value={cfg.title} onChange={(e) => setCfg({ ...cfg, title: e.target.value })} />
          </label>
          <label>
            <span className="label">Intro (optional)</span>
            <input className="field" value={cfg.intro} onChange={(e) => setCfg({ ...cfg, intro: e.target.value })} />
          </label>
        </div>
        <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0">
          <legend className="label">Services on the page</legend>
          {summary?.services.map((s) => (
            <button
              type="button"
              key={s.id}
              className="chip"
              aria-pressed={cfg.services.includes(s.id)}
              onClick={() => setCfg({ ...cfg, services: cfg.services.includes(s.id) ? cfg.services.filter((x) => x !== s.id) : [...cfg.services, s.id] })}
            >
              {s.name}
            </button>
          ))}
        </fieldset>
        <div className="flex items-center gap-3">
          <button className="btn btn-primary">Save status page</button>
          {msg && <span role="status" className="text-sm">{msg}</span>}
        </div>
      </form>
    </Card>
  );
}

export function IntegrationsInfo() {
  const { summary } = usePulse();
  const origin = window.location.origin;
  const first = summary?.services[0]?.id ?? "github";
  const rows: [string, string, string][] = [
    ["Prometheus metrics", `${origin}/metrics`, "Scrape with Prometheus; a Grafana dashboard is in deploy/grafana-dashboard.json."],
    ["Incident feed (Atom)", `${origin}/feed/incidents.atom`, "Subscribe in any feed reader."],
    ["Maintenance calendar (iCal)", `${origin}/feed/maintenance.ics`, "Add to your calendar app as a subscription."],
    ["Status badge", `${origin}/badge/${first}.svg`, `Embed: ![status](${origin}/badge/${first}.svg)`],
    ["REST API", `${origin}/api/docs`, "Interactive OpenAPI documentation."],
  ];
  return (
    <Card title="Integrations" intro="Home Assistant: set PULSE_MQTT_URL and every watched service appears as a device through MQTT discovery (see docs/INTEGRATIONS.md).">
      <dl className="m-0 grid gap-3 text-sm">
        {rows.map(([k, url, note]) => (
          <div key={k}>
            <dt className="font-semibold">{k}</dt>
            <dd className="m-0">
              <a href={url} target="_blank" rel="noreferrer">
                {url}
              </a>
              <span className="block" style={{ color: "var(--muted)" }}>
                {note}
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
