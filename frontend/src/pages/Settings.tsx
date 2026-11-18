import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { useApi, usePulse } from "../api/store";
import { applyTheme, setPrefs, usePrefs, type Theme } from "../lib/prefs";

interface SettingsResp {
  editable: Record<string, string | number | boolean>;
  overrides: Record<string, unknown>;
  locked: string[];
  server: Record<string, unknown> & { version: string; database: string; features: Record<string, boolean> };
  stats: Record<string, number>;
}

export function Card({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <section className="panel rounded-lg p-5">
      <h2 className="mb-1 mt-0 text-lg font-bold">{title}</h2>
      {intro && (
        <p className="mb-4 mt-0 max-w-[70ch] text-sm" style={{ color: "var(--muted)" }}>
          {intro}
        </p>
      )}
      {children}
    </section>
  );
}

const FIELDS: { key: string; label: string; help: string; type: "bool" | "text" | "number" }[] = [
  { key: "public_view", label: "Anyone can view the dashboard", help: "When off, viewing requires signing in.", type: "bool" },
  { key: "allow_private_targets", label: "Allow checks of local network addresses", help: "Needed for homelab services such as 192.168.x.x.", type: "bool" },
  { key: "canary_targets", label: "Connectivity test sites", help: "Comma-separated URLs. When most fail, Pulse decides you're offline.", type: "text" },
  { key: "dns_resolvers", label: "Public DNS resolvers", help: "Compared with your own resolver when a DNS check fails.", type: "text" },
  { key: "feed_interval_s", label: "Status feed interval (seconds)", help: "How often provider status pages are fetched.", type: "number" },
  { key: "probe_interval_s", label: "Check interval (seconds)", help: "Default for new checks; applies after the next restart for existing ones.", type: "number" },
  { key: "fail_threshold", label: "Failures before a check counts as down", help: "Consecutive failures.", type: "number" },
  { key: "recover_threshold", label: "Good rounds before recovering", help: "Avoids flapping back to Operational.", type: "number" },
  { key: "latency_factor", label: "Slow when latency exceeds this multiple of normal (p95)", help: "", type: "number" },
  { key: "latency_min_ms", label: "Ignore slowness below (ms)", help: "", type: "number" },
  { key: "retention_raw_days", label: "Keep individual check results (days)", help: "", type: "number" },
  { key: "retention_5m_days", label: "Keep 5-minute summaries (days)", help: "", type: "number" },
  { key: "retention_incident_days", label: "Keep resolved incidents (days)", help: "", type: "number" },
];

function MonitoringSettings() {
  const { data, reload } = useApi<SettingsResp>("/api/v1/settings");
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data) setValues(data.editable);
  }, [data]);
  if (!data) return null;

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!data) return;
    const changed = Object.fromEntries(Object.entries(values).filter(([k, v]) => data.editable[k] !== v && !data.locked.includes(k)));
    if (!Object.keys(changed).length) return setMsg("Nothing changed.");
    try {
      await api("/api/v1/settings", { method: "PUT", json: changed });
      setMsg("Settings saved.");
      reload();
    } catch (ex) {
      setMsg((ex as Error).message);
    }
  }

  return (
    <Card title="Monitoring" intro="These override the environment variables. Settings fixed in pulse.yaml can't be changed here.">
      <form onSubmit={save} className="grid gap-4 sm:grid-cols-2">
        {FIELDS.map((f) => {
          const locked = data.locked.includes(f.key);
          const v = values[f.key];
          return (
            <label key={f.key} className={f.type === "bool" ? "flex items-start gap-2 sm:col-span-2" : ""}>
              {f.type === "bool" ? (
                <>
                  <input type="checkbox" className="mt-1" checked={Boolean(v)} disabled={locked} onChange={(e) => setValues({ ...values, [f.key]: e.target.checked })} />
                  <span>
                    {f.label}
                    <span className="block text-sm" style={{ color: "var(--muted)" }}>
                      {f.help}
                    </span>
                  </span>
                </>
              ) : (
                <>
                  <span className="label">
                    {f.label}
                    {locked ? " (set in pulse.yaml)" : ""}
                  </span>
                  <input
                    className="field"
                    disabled={locked}
                    type={f.type === "number" ? "number" : "text"}
                    step="any"
                    value={String(v ?? "")}
                    onChange={(e) => setValues({ ...values, [f.key]: f.type === "number" ? Number(e.target.value) : e.target.value })}
                  />
                  {f.help && (
                    <span className="mt-1 block text-[0.8125rem]" style={{ color: "var(--muted)" }}>
                      {f.help}
                    </span>
                  )}
                </>
              )}
            </label>
          );
        })}
        <div className="flex items-center gap-3 sm:col-span-2">
          <button className="btn btn-primary">Save settings</button>
          {msg && (
            <span role="status" className="text-sm" style={{ color: "var(--muted)" }}>
              {msg}
            </span>
          )}
        </div>
      </form>
      <dl className="narrow mt-6 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" style={{ color: "var(--muted)" }}>
        <dt>Version</dt>
        <dd className="m-0">{data.server.version}</dd>
        <dt>Database</dt>
        <dd className="m-0">
          {data.server.database}, {data.stats.probe_results?.toLocaleString()} check results, {data.stats.incidents?.toLocaleString()} incidents
        </dd>
        <dt>Features</dt>
        <dd className="m-0">
          {Object.entries(data.server.features)
            .map(([k, on]) => `${k} ${on ? "on" : "off"}`)
            .join(", ")}
        </dd>
      </dl>
    </Card>
  );
}

function Account() {
  const [msg, setMsg] = useState<string | null>(null);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      await api("/api/v1/auth/password", { method: "POST", json: { current_password: f.get("current"), new_password: f.get("next") } });
      setMsg("Password changed.");
      e.currentTarget.reset();
    } catch (ex) {
      setMsg((ex as Error).message);
    }
  }
  return (
    <Card title="Account">
      <form onSubmit={submit} className="grid max-w-md gap-3">
        <label>
          <span className="label">Current password</span>
          <input name="current" type="password" className="field" required autoComplete="current-password" />
        </label>
        <label>
          <span className="label">New password (at least 10 characters)</span>
          <input name="next" type="password" className="field" required minLength={10} autoComplete="new-password" />
        </label>
        <div className="flex items-center gap-3">
          <button className="btn btn-primary">Change password</button>
          {msg && <span role="status" className="text-sm">{msg}</span>}
        </div>
      </form>
    </Card>
  );
}

function Backup() {
  const { refresh } = usePulse();
  const [msg, setMsg] = useState<string | null>(null);
  async function exportConfig() {
    const data = await api<unknown>("/api/v1/config/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `pulse-config-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  async function importConfig(file: File, replace: boolean) {
    try {
      const json = JSON.parse(await file.text());
      const res = await api<{ tracked: number; custom: number }>("/api/v1/config/import", { method: "POST", json: { ...json, replace } });
      setMsg(`Imported ${res.tracked} catalog services and ${res.custom} of your own.`);
      await refresh();
    } catch (ex) {
      setMsg((ex as Error).message);
    }
  }
  return (
    <Card title="Import and export" intro="Exports contain watched services, your own services and settings. They never contain passwords or alert URLs.">
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn" onClick={exportConfig}>
          Download export
        </button>
        <label className="btn">
          Import and merge
          <input type="file" accept="application/json" className="sr-only" onChange={(e) => e.target.files?.[0] && importConfig(e.target.files[0], false)} />
        </label>
        <label className="btn">
          Import and replace watched list
          <input type="file" accept="application/json" className="sr-only" onChange={(e) => e.target.files?.[0] && importConfig(e.target.files[0], true)} />
        </label>
        {msg && <span role="status" className="text-sm">{msg}</span>}
      </div>
    </Card>
  );
}

function Appearance() {
  const prefs = usePrefs();
  const themes: [Theme, string][] = [
    ["system", "Follow system"],
    ["dark", "Dark"],
    ["light", "Light"],
    ["contrast", "High contrast"],
  ];
  return (
    <Card title="Appearance" intro="Saved in this browser only.">
      <div className="grid gap-4 sm:grid-cols-3">
        <fieldset className="m-0 border-0 p-0">
          <legend className="label">Theme</legend>
          {themes.map(([t, l]) => (
            <label key={t} className="flex items-center gap-2 py-0.5">
              <input
                type="radio"
                name="theme"
                checked={prefs.theme === t}
                onChange={() => {
                  setPrefs({ theme: t });
                  applyTheme(t);
                }}
              />
              {l}
            </label>
          ))}
        </fieldset>
        <fieldset className="m-0 border-0 p-0">
          <legend className="label">Tiles</legend>
          {(["comfortable", "compact"] as const).map((d) => (
            <label key={d} className="flex items-center gap-2 py-0.5 capitalize">
              <input type="radio" name="density" checked={prefs.density === d} onChange={() => setPrefs({ density: d })} />
              {d}
            </label>
          ))}
        </fieldset>
        <fieldset className="m-0 border-0 p-0">
          <legend className="label">On phones</legend>
          {([1, 2] as const).map((c) => (
            <label key={c} className="flex items-center gap-2 py-0.5">
              <input type="radio" name="cols" checked={prefs.mobileCols === c} onChange={() => setPrefs({ mobileCols: c })} />
              {c === 1 ? "One column" : "Two columns"}
            </label>
          ))}
        </fieldset>
      </div>
      <p className="mb-0 mt-4 text-sm" style={{ color: "var(--muted)" }}>
        For a wall display, open <a href="/?kiosk=1">/?kiosk=1</a>. It hides the menus and rotates through problems, the map and all services.
      </p>
    </Card>
  );
}

import AgentsSettings from "../components/AgentsSettings";

export const adminSections: { id: string; render: () => ReactNode }[] = [{ id: "agents", render: () => <AgentsSettings /> }];

export default function Settings() {
  const { isAdmin, me } = usePulse();
  return (
    <div className="grid max-w-5xl gap-4">
      <h1 className="wide m-0 text-3xl font-extrabold">Settings</h1>
      <Appearance />
      {isAdmin ? (
        <>
          {adminSections.map((s) => (
            <div key={s.id}>{s.render()}</div>
          ))}
          <MonitoringSettings />
          <Backup />
          {me?.user?.via === "session" && <Account />}
        </>
      ) : (
        <p style={{ color: "var(--muted)" }}>Sign in to manage alerts, agents, monitoring and backups.</p>
      )}
    </div>
  );
}
