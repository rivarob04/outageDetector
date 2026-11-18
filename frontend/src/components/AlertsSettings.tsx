import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApi, usePulse } from "../api/store";
import { dateTime } from "../lib/format";
import { Card } from "../pages/Settings";

interface Channel {
  id: number;
  name: string;
  kind: "apprise" | "webhook";
  url: string;
  enabled: boolean;
  has_secret: boolean;
}
interface RuleRow {
  id: number;
  channel_id: number;
  services: string[];
  categories: string[];
  min_state: string;
  scopes: string[];
  quiet_start: string | null;
  quiet_end: string | null;
  tz: string;
  cooldown_s: number;
  recovery: boolean;
  digest: boolean;
  enabled: boolean;
}
interface Overview {
  channels: Channel[];
  rules: RuleRow[];
  log: { id: number; channel_id: number | null; service_id: string | null; kind: string; title: string; ts: string; ok: boolean; error: string | null }[];
  options: { categories: Record<string, string> };
}

const SEVERITY = [
  ["degraded", "Degraded or worse"],
  ["partial_outage", "Partial outage or worse"],
  ["major_outage", "Major outages only"],
] as const;
const SCOPES = [
  ["provider_confirmed", "Confirmed by the provider"],
  ["widespread", "Failing from several places"],
  ["local_only", "Failing only from my network"],
] as const;

function RuleEditor({ rule, onSaved }: { rule: RuleRow; onSaved: () => void }) {
  const { summary } = usePulse();
  const [r, setR] = useState(rule);
  const [msg, setMsg] = useState<string | null>(null);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      const { id: _id, ...body } = r;
      void _id;
      await api(`/api/v1/alerts/rules/${rule.id}`, { method: "PUT", json: { ...body, quiet_start: r.quiet_start || null, quiet_end: r.quiet_end || null } });
      setMsg("Saved.");
      onSaved();
    } catch (ex) {
      setMsg((ex as Error).message);
    }
  }

  return (
    <form onSubmit={save} className="grid gap-3 rounded-md p-3 sm:grid-cols-2" style={{ background: "var(--bg)", border: "1px solid var(--line)" }}>
      <label>
        <span className="label">Alert me about</span>
        <select className="field" value={r.min_state} onChange={(e) => setR({ ...r, min_state: e.target.value })}>
          {SEVERITY.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="label">For</span>
        <select
          className="field"
          value={r.services.length ? "services" : r.categories.length ? "categories" : "all"}
          onChange={(e) => setR({ ...r, services: e.target.value === "services" ? (summary?.services.slice(0, 1).map((s) => s.id) ?? []) : [], categories: e.target.value === "categories" ? ["cloud"] : [] })}
        >
          <option value="all">All watched services</option>
          <option value="categories">Some categories</option>
          <option value="services">Some services</option>
        </select>
      </label>
      {r.categories.length > 0 && (
        <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0 sm:col-span-2">
          <legend className="label">Categories</legend>
          {Array.from(new Set(summary?.services.map((s) => s.category) ?? [])).map((c) => (
            <button type="button" key={c} className="chip" aria-pressed={r.categories.includes(c)} onClick={() => setR({ ...r, categories: r.categories.includes(c) ? r.categories.filter((x) => x !== c) : [...r.categories, c] })}>
              {summary?.categories[c] ?? c}
            </button>
          ))}
        </fieldset>
      )}
      {r.services.length > 0 && (
        <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0 sm:col-span-2">
          <legend className="label">Services</legend>
          {summary?.services.map((s) => (
            <button type="button" key={s.id} className="chip" aria-pressed={r.services.includes(s.id)} onClick={() => setR({ ...r, services: r.services.includes(s.id) ? r.services.filter((x) => x !== s.id) : [...r.services, s.id] })}>
              {s.name}
            </button>
          ))}
        </fieldset>
      )}
      <fieldset className="m-0 border-0 p-0 sm:col-span-2">
        <legend className="label">When the problem is</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          {SCOPES.map(([v, l]) => (
            <label key={v} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={r.scopes.includes(v)} onChange={(e) => setR({ ...r, scopes: e.target.checked ? [...r.scopes, v] : r.scopes.filter((x) => x !== v) })} />
              {l}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid grid-cols-[1fr_1fr_1.4fr] gap-2 sm:col-span-2">
        <label>
          <span className="label">Quiet from</span>
          <input type="time" className="field" value={r.quiet_start ?? ""} onChange={(e) => setR({ ...r, quiet_start: e.target.value || null })} />
        </label>
        <label>
          <span className="label">until</span>
          <input type="time" className="field" value={r.quiet_end ?? ""} onChange={(e) => setR({ ...r, quiet_end: e.target.value || null })} />
        </label>
        <label>
          <span className="label">Time zone</span>
          <input className="field" value={r.tz} onChange={(e) => setR({ ...r, tz: e.target.value })} placeholder={tz} list="tz-suggest" />
          <datalist id="tz-suggest">
            <option value={tz} />
            <option value="UTC" />
          </datalist>
        </label>
      </div>
      <label>
        <span className="label">Don't repeat within (minutes)</span>
        <input type="number" min={0} max={1440} className="field" value={Math.round(r.cooldown_s / 60)} onChange={(e) => setR({ ...r, cooldown_s: Number(e.target.value) * 60 })} />
      </label>
      <div className="grid content-end gap-1 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={r.recovery} onChange={(e) => setR({ ...r, recovery: e.target.checked })} /> Tell me when it recovers
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={r.digest} onChange={(e) => setR({ ...r, digest: e.target.checked })} /> Daily digest at 08:00
        </label>
      </div>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button className="btn btn-primary">Save rule</button>
        {msg && <span role="status" className="text-sm">{msg}</span>}
      </div>
    </form>
  );
}

export default function AlertsSettings() {
  const { data, reload } = useApi<Overview>("/api/v1/alerts");
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<"apprise" | "webhook">("apprise");
  const [msg, setMsg] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);

  async function add(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      const res = await api<{ id: number; webhook_secret: string | null }>("/api/v1/alerts/channels", {
        method: "POST",
        json: { name: f.get("name"), kind, url: f.get("url") },
      });
      setSecret(res.webhook_secret);
      setAdding(false);
      reload();
    } catch (ex) {
      setMsg((ex as Error).message);
    }
  }

  async function test(id: number) {
    setMsg("Sending…");
    try {
      await api(`/api/v1/alerts/channels/${id}/test`, { method: "POST" });
      setMsg("Test notification sent.");
    } catch (ex) {
      setMsg((ex as Error).message);
    }
    reload();
  }

  if (!data) return null;
  return (
    <Card
      title="Alerts"
      intro="Pulse sends alerts through Apprise, which supports ntfy, Discord, Slack, Telegram, Pushover, Gotify, email, Matrix and many more, or to your own webhook. Alerts pause while you're offline; you get one summary when you're back."
    >
      {secret && (
        <p className="mt-0 rounded p-3 text-sm" style={{ border: "1px solid var(--line)" }}>
          Webhook signing secret (shown once): <code>{secret}</code>. Pulse signs each request with <code>X-Pulse-Signature: sha256=HMAC(secret, timestamp + "." + body)</code>.
        </p>
      )}
      <div className="grid gap-4">
        {data.channels.map((c) => (
          <div key={c.id} className="grid gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{c.name}</span>
              <code className="text-xs" style={{ color: "var(--muted)" }}>
                {c.url}
              </code>
              <span className="ml-auto flex gap-2">
                <button className="btn" onClick={() => test(c.id)}>
                  Send test
                </button>
                <button
                  className="btn btn-danger"
                  onClick={async () => {
                    if (!window.confirm(`Delete ${c.name}?`)) return;
                    await api(`/api/v1/alerts/channels/${c.id}`, { method: "DELETE" });
                    reload();
                  }}
                >
                  Delete
                </button>
              </span>
            </div>
            {data.rules
              .filter((r) => r.channel_id === c.id)
              .map((r) => (
                <RuleEditor key={r.id} rule={r} onSaved={reload} />
              ))}
          </div>
        ))}
      </div>
      {msg && (
        <p role="status" className="text-sm">
          {msg}
        </p>
      )}
      {adding ? (
        <form onSubmit={add} className="mt-4 grid items-end gap-3 sm:grid-cols-[1fr_140px_2fr_auto]">
          <label>
            <span className="label">Name</span>
            <input name="name" className="field" required placeholder="My phone" />
          </label>
          <label>
            <span className="label">Type</span>
            <select className="field" value={kind} onChange={(e) => setKind(e.target.value as "apprise" | "webhook")}>
              <option value="apprise">Apprise URL</option>
              <option value="webhook">Webhook</option>
            </select>
          </label>
          <label>
            <span className="label">{kind === "apprise" ? "Apprise URL, for example ntfys://ntfy.sh/my-topic" : "Webhook URL"}</span>
            <input name="url" className="field" required placeholder={kind === "apprise" ? "discord://webhook_id/webhook_token" : "https://example.com/hooks/pulse"} />
          </label>
          <button className="btn btn-primary">Add channel</button>
        </form>
      ) : (
        <button className="btn btn-primary mt-4" onClick={() => setAdding(true)}>
          Add a channel
        </button>
      )}
      {data.log.length > 0 && (
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-semibold">Recent notifications</summary>
          <ul className="mt-2 grid gap-1 p-0 text-sm">
            {data.log.map((l) => (
              <li key={l.id} className="flex list-none gap-3">
                <span className="narrow w-28 shrink-0" style={{ color: "var(--muted)" }}>
                  {dateTime(l.ts)}
                </span>
                <span style={{ color: l.ok ? "var(--text)" : "var(--major)" }}>
                  {l.title}
                  {l.error ? `: ${l.error}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
