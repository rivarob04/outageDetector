import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi, usePulse } from "../api/store";
import type { CatalogItem, ServiceDetail } from "../api/types";
import { Monogram } from "../components/Status";

interface CatalogResp {
  categories: Record<string, string>;
  items: CatalogItem[];
  custom: CatalogItem[];
}

type ProbeKind = "http" | "dns" | "tcp" | "tls";
interface ProbeDraft {
  kind: ProbeKind;
  url: string;
  host: string;
  port: string;
  accept: string;
  keyword: string;
}
const EMPTY_PROBE: ProbeDraft = { kind: "http", url: "", host: "", port: "", accept: "", keyword: "" };

export default function Catalog() {
  const { isAdmin, refresh } = usePulse();
  const { data, reload } = useApi<CatalogResp>("/api/v1/catalog");
  const [params] = useSearchParams();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const editId = params.get("edit");

  const items = useMemo(() => {
    if (!data) return [];
    const n = q.trim().toLowerCase();
    return data.items.filter((i) => (cat === "all" || i.category === cat) && (!n || i.name.toLowerCase().includes(n)));
  }, [data, q, cat]);

  async function toggle(item: CatalogItem) {
    setBusy(item.id);
    setErr(null);
    try {
      await api(`/api/v1/services/${item.id}/track`, { method: item.tracked ? "DELETE" : "POST" });
      reload();
      await refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!data) return <p style={{ color: "var(--muted)" }}>Loading…</p>;
  const cats = Array.from(new Set(data.items.map((i) => i.category)));

  return (
    <div className="grid gap-6">
      <div>
        <h1 className="wide mb-1 mt-0 text-3xl font-extrabold">Add services</h1>
        <p className="m-0 max-w-[68ch]" style={{ color: "var(--muted)" }}>
          Pick the services you depend on. Pulse only checks services you watch. Each one combines the provider's own status feed with checks from your network, where both exist.
        </p>
        {!isAdmin && (
          <p className="mb-0 mt-2 text-sm" style={{ color: "var(--degraded)" }}>
            Sign in to change what's watched.
          </p>
        )}
      </div>

      {isAdmin && <CustomForm editId={editId} onSaved={() => { reload(); void refresh(); }} />}

      {data.custom.length > 0 && (
        <section>
          <h2 className="mb-2 mt-0 text-lg font-bold">Your services</h2>
          <ul className="m-0 grid gap-2 p-0 sm:grid-cols-2 lg:grid-cols-3">
            {data.custom.map((c) => (
              <li key={c.id} className="panel flex list-none items-center gap-3 rounded-lg p-3">
                <Monogram id={c.id} name={c.name} />
                <Link to={`/service/${c.id}`} className="min-w-0 flex-1 truncate font-semibold">
                  {c.name}
                </Link>
                {c.source === "config" ? (
                  <span className="narrow text-xs" style={{ color: "var(--muted)" }}>
                    from pulse.yaml
                  </span>
                ) : (
                  isAdmin && (
                    <Link className="btn no-underline" to={`/catalog?edit=${c.id}`}>
                      Edit
                    </Link>
                  )
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2 className="m-0 mr-2 text-lg font-bold">Catalog</h2>
          <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-1.5">
            <button className="chip" aria-pressed={cat === "all"} onClick={() => setCat("all")}>
              All
            </button>
            {cats.map((c) => (
              <button key={c} className="chip" aria-pressed={cat === c} onClick={() => setCat(c)}>
                {data.categories[c] ?? c}
              </button>
            ))}
          </div>
          <input id="search" className="field ml-auto w-48" placeholder="Search  /" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the catalog" />
        </div>
        {err && (
          <p role="alert" style={{ color: "var(--major)" }}>
            {err}
          </p>
        )}
        <ul className="m-0 grid gap-2 p-0 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((i) => (
            <li key={i.id} className="panel flex list-none items-center gap-3 rounded-lg p-3">
              <Monogram id={i.id} name={i.name} />
              <div className="min-w-0 flex-1">
                <Link to={`/service/${i.id}`} className="block truncate font-semibold no-underline hover:underline">
                  {i.name}
                </Link>
                <span className="narrow text-[0.8125rem]" style={{ color: "var(--muted)" }}>
                  {i.signal === "feed+checks" ? "Status feed and checks" : i.signal === "feed" ? "Status feed" : "Checks only"}
                  {i.verified === false ? ", not verified recently" : ""}
                </span>
              </div>
              {isAdmin && (
                <button className={`btn ${i.tracked ? "" : "btn-primary"}`} disabled={busy === i.id} onClick={() => toggle(i)} aria-pressed={i.tracked}>
                  {i.tracked ? "Watching" : "Watch"}
                </button>
              )}
            </li>
          ))}
        </ul>
        <p className="text-sm" style={{ color: "var(--muted)" }}>
          Missing a service? Add it as your own above, or{" "}
          <a href="https://github.com/rivarob04/outageDetector/issues/new?template=request-service.yml" target="_blank" rel="noreferrer">
            request it for the catalog
          </a>
          .
        </p>
      </section>
    </div>
  );
}

function CustomForm({ editId, onSaved }: { editId: string | null; onSaved: () => void }) {
  const navigate = useNavigate();
  const { data: existing } = useApi<ServiceDetail>(editId ? `/api/v1/services/${editId}` : null);
  const [open, setOpen] = useState(Boolean(editId));
  const [name, setName] = useState("");
  const [category, setCategory] = useState("custom");
  const [probes, setProbes] = useState<ProbeDraft[]>([{ ...EMPTY_PROBE }]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!existing?.config) return;
    const cfg = existing.config as { name: string; category: string; probes: Record<string, unknown>[] };
    setOpen(true);
    setName(cfg.name);
    setCategory(cfg.category);
    setProbes(
      cfg.probes.map((p) => ({
        kind: p.kind as ProbeKind,
        url: String(p.url ?? ""),
        host: String(p.host ?? ""),
        port: p.port ? String(p.port) : "",
        accept: ((p.accept_status as number[]) ?? []).join(", "),
        keyword: String(p.keyword ?? ""),
      })),
    );
  }, [existing]);

  function update(i: number, patch: Partial<ProbeDraft>) {
    setProbes((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const body = {
      name,
      category,
      probes: probes.map((p) => ({
        kind: p.kind,
        ...(p.kind === "http" ? { url: p.url.trim() } : { host: p.host.trim() }),
        ...(p.port ? { port: Number(p.port) } : {}),
        ...(p.accept.trim() ? { accept_status: p.accept.split(/[ ,]+/).filter(Boolean).map(Number) } : {}),
        ...(p.keyword.trim() && p.kind === "http" ? { keyword: p.keyword.trim() } : {}),
      })),
    };
    try {
      const res = await api<{ id: string }>(editId ? `/api/v1/services/${editId}` : "/api/v1/services", {
        method: editId ? "PUT" : "POST",
        json: body,
      });
      onSaved();
      navigate(`/service/${res.id}`);
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div>
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          Add your own service
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="panel grid gap-4 rounded-lg p-5">
      <h2 className="m-0 text-lg font-bold">{editId ? `Edit ${name || "service"}` : "Add your own service"}</h2>
      <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
        Watch any website, API or homelab service. Addresses on your local network only work when private targets are allowed in Settings.
      </p>
      <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
        <label>
          <span className="label">Name</span>
          <input className="field" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Home NAS" />
        </label>
        <label>
          <span className="label">Category</span>
          <select className="field" value={category} onChange={(e) => setCategory(e.target.value)}>
            {["custom", "network", "smarthome", "streaming", "devtools", "cloud", "productivity"].map((c) => (
              <option key={c} value={c}>
                {c === "custom" ? "My services" : c}
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="m-0 grid gap-3 border-0 p-0">
        <legend className="label">Checks</legend>
        {probes.map((p, i) => (
          <div key={i} className="grid items-end gap-2 sm:grid-cols-[110px_1fr_110px_1fr_auto]">
            <label>
              <span className="label">Type</span>
              <select className="field" value={p.kind} onChange={(e) => update(i, { kind: e.target.value as ProbeKind })}>
                <option value="http">HTTP(S)</option>
                <option value="dns">DNS</option>
                <option value="tcp">TCP port</option>
                <option value="tls">TLS certificate</option>
              </select>
            </label>
            {p.kind === "http" ? (
              <label>
                <span className="label">URL</span>
                <input className="field" required type="url" value={p.url} onChange={(e) => update(i, { url: e.target.value })} placeholder="https://example.com/health" />
              </label>
            ) : (
              <label>
                <span className="label">Host name</span>
                <input className="field" required value={p.host} onChange={(e) => update(i, { host: e.target.value })} placeholder="example.com" />
              </label>
            )}
            {p.kind === "http" ? (
              <label>
                <span className="label">Also OK</span>
                <input className="field" value={p.accept} onChange={(e) => update(i, { accept: e.target.value })} placeholder="401, 403" title="2xx and 3xx always count as up" />
              </label>
            ) : (
              <label>
                <span className="label">Port</span>
                <input className="field" type="number" min={1} max={65535} required={p.kind === "tcp"} value={p.port} onChange={(e) => update(i, { port: e.target.value })} placeholder={p.kind === "tls" ? "443" : ""} />
              </label>
            )}
            {p.kind === "http" ? (
              <label>
                <span className="label">Page must contain (optional)</span>
                <input className="field" value={p.keyword} onChange={(e) => update(i, { keyword: e.target.value })} />
              </label>
            ) : (
              <span />
            )}
            <button type="button" className="btn" disabled={probes.length === 1} onClick={() => setProbes((ps) => ps.filter((_, j) => j !== i))} aria-label={`Remove check ${i + 1}`}>
              Remove
            </button>
          </div>
        ))}
        <div>
          <button type="button" className="btn" onClick={() => setProbes((ps) => [...ps, { ...EMPTY_PROBE }])} disabled={probes.length >= 10}>
            Add another check
          </button>
        </div>
      </fieldset>
      {err && (
        <p role="alert" className="m-0 text-sm" style={{ color: "var(--major)" }}>
          {err}
        </p>
      )}
      <div className="flex gap-2">
        <button className="btn btn-primary" disabled={busy}>
          {editId ? "Save changes" : "Add service"}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setOpen(false);
            navigate("/catalog");
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
