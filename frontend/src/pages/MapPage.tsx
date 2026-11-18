import maplibregl, { type GeoJSONSource, type Map as MLMap, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Link } from "react-router-dom";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import countriesTopo from "world-atlas/countries-110m.json";
import { usePulse, useApi } from "../api/store";
import type { State } from "../api/types";
import { Glyph } from "../components/Status";
import { duration, ms } from "../lib/format";
import { STATE_LABEL, STATE_RANK, isProblem } from "../lib/status";

interface MapRegion {
  id: string;
  name: string;
  provider: string;
  lat: number;
  lon: number;
  state: State;
  items: { service_id: string; service_name: string; state: State; what: string }[];
}
interface MapAgent {
  id: string;
  name: string;
  location: string | null;
  lat: number;
  lon: number;
  fresh: boolean;
  last_seen_at: string | null;
  ok_checks: number;
  failing: { service_id: string; service_name: string }[];
  median_ms: number | null;
}
interface MapData {
  at: string;
  replay: boolean;
  style_url: string | null;
  providers: string[];
  regions: MapRegion[];
  agents: MapAgent[];
  host: { lat: number; lon: number; name: string; offline: boolean } | null;
}

const PROVIDER_LABEL: Record<string, string> = { aws: "AWS", gcp: "Google Cloud", cloudflare: "Cloudflare" };
/** Unwrap rings that cross the antimeridian so they don't smear across the map; drop Antarctica. */
function cleanCountries(): GeoJSON.FeatureCollection {
  const fc = feature(countriesTopo as unknown as Topology, (countriesTopo as unknown as Topology).objects.countries) as unknown as GeoJSON.FeatureCollection;
  const unwrap = (ring: number[][]) => {
    let offset = 0;
    return ring.map(([lon, lat], i) => {
      if (i > 0) {
        const prev = ring[i - 1][0];
        if (lon - prev > 180) offset -= 360;
        else if (prev - lon > 180) offset += 360;
      }
      return [lon + offset, lat];
    });
  };
  return {
    type: "FeatureCollection",
    features: fc.features
      .filter((f) => String(f.id) !== "010")
      .map((f) => {
        const g = f.geometry;
        if (g.type === "Polygon") return { ...f, geometry: { ...g, coordinates: g.coordinates.map(unwrap) } };
        if (g.type === "MultiPolygon") return { ...f, geometry: { ...g, coordinates: g.coordinates.map((p) => p.map(unwrap)) } };
        return f;
      }),
  };
}
const countries = cleanCountries();

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function baseStyle(): StyleSpecification {
  return {
    version: 8,
    sources: { countries: { type: "geojson", data: countries } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": cssVar("--bg") } },
      { id: "land", type: "fill", source: "countries", paint: { "fill-color": cssVar("--panel-2") } },
      { id: "borders", type: "line", source: "countries", paint: { "line-color": cssVar("--line"), "line-width": 0.6 } },
    ],
  };
}

function stateColorExpr(): maplibregl.ExpressionSpecification {
  return [
    "match",
    ["get", "state"],
    "operational", cssVar("--ok"),
    "degraded", cssVar("--degraded"),
    "partial_outage", cssVar("--partial"),
    "major_outage", cssVar("--major"),
    "maintenance", cssVar("--maint"),
    cssVar("--unknown"),
  ];
}

type Selected = { kind: "region"; item: MapRegion } | { kind: "agent"; item: MapAgent } | null;

export default function MapPage({ kiosk = false }: { kiosk?: boolean }) {
  const { summary } = usePulse();
  const [minutesAgo, setMinutesAgo] = useState(0);
  const [debounced, setDebounced] = useState(0);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Selected>(null);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(minutesAgo), 250);
    return () => window.clearTimeout(t);
  }, [minutesAgo]);
  const path = debounced ? `/api/v1/map?at=${encodeURIComponent(new Date(Date.now() - debounced * 60000).toISOString())}` : "/api/v1/map";
  const { data } = useApi<MapData>(path, [summary?.generated_at]);

  const regions = useMemo(() => (data?.regions ?? []).filter((r) => !hidden.has(r.provider)), [data, hidden]);
  const problems = regions.filter((r) => r.state !== "operational");

  return (
    <div className={kiosk ? "" : "grid gap-4"}>
      {!kiosk && (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <div>
            <h1 className="wide m-0 text-3xl font-extrabold">Map</h1>
            <p className="m-0 mt-1 max-w-[70ch] text-sm" style={{ color: "var(--muted)" }}>
              Provider regions colored by what their status feeds report, plus your vantage agents and what they can reach.
            </p>
          </div>
          {data && data.providers.length > 1 && (
            <div role="group" aria-label="Show providers" className="flex flex-wrap gap-1.5">
              {data.providers.map((p) => (
                <button
                  key={p}
                  className="chip"
                  aria-pressed={!hidden.has(p)}
                  onClick={() =>
                    setHidden((h) => {
                      const n = new Set(h);
                      if (n.has(p)) n.delete(p);
                      else n.add(p);
                      return n;
                    })
                  }
                >
                  {PROVIDER_LABEL[p] ?? p}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className={`grid gap-4 ${kiosk ? "" : "lg:grid-cols-[minmax(0,1fr)_320px]"}`}>
        <div className="panel relative overflow-hidden rounded-lg">
          <MapCanvas data={data} regions={regions} onSelect={setSelected} height={kiosk ? "70vh" : "min(62vh, 640px)"} />
          {data?.replay && (
            <div className="narrow absolute left-3 top-3 rounded px-2 py-1 text-sm font-semibold" style={{ background: "var(--bg)", border: "1px solid var(--maint)", color: "var(--maint)" }}>
              Replaying {new Date(data.at).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" })}
            </div>
          )}
        </div>
        {!kiosk && (
          <aside className="grid content-start gap-3">
            {selected ? <SelectedPanel sel={selected} onClose={() => setSelected(null)} /> : null}
            <section className="panel rounded-lg p-4">
              <h2 className="mb-2 mt-0 text-base font-bold">Affected regions</h2>
              {problems.length === 0 ? (
                <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
                  {data ? "No region-level problems reported." : "Loading…"}
                </p>
              ) : (
                <ul className="m-0 grid gap-1.5 p-0">
                  {problems.map((r) => (
                    <li key={r.id} className="list-none">
                      <button className="flex w-full items-center gap-2 bg-transparent p-0 text-left text-sm" style={{ border: 0, color: "inherit", cursor: "pointer" }} onClick={() => setSelected({ kind: "region", item: r })}>
                        <Glyph state={r.state} size={10} />
                        <span className="truncate">{r.name}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="panel rounded-lg p-4">
              <h2 className="mb-2 mt-0 text-base font-bold">Vantage agents</h2>
              {!data?.agents.length ? (
                <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
                  No agents with a location yet. Run <code>pulse agent</code> on another network to see outages from more than one place.{" "}
                  <Link to="/settings">Add an agent</Link>
                </p>
              ) : (
                <ul className="m-0 grid gap-2 p-0">
                  {data.agents.map((a) => {
                    const st: State = !a.fresh ? "unknown" : a.failing.length ? "major_outage" : "operational";
                    return (
                      <li key={a.id} className="list-none">
                        <button className="flex w-full items-center gap-2 bg-transparent p-0 text-left text-sm" style={{ border: 0, color: "inherit", cursor: "pointer" }} onClick={() => setSelected({ kind: "agent", item: a })}>
                          <Glyph state={st} size={10} />
                          <span className="flex-1 truncate">{a.name}</span>
                          <span className="narrow" style={{ color: "var(--muted)" }}>
                            {!a.fresh ? "offline" : a.failing.length ? `${a.failing.length} failing` : ms(a.median_ms)}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </aside>
        )}
      </div>
      {!kiosk && (
        <div className="panel flex flex-wrap items-center gap-3 rounded-lg px-4 py-3">
          <label htmlFor="scrub" className="text-sm font-semibold">
            Replay the last 24 hours
          </label>
          <input
            id="scrub"
            type="range"
            min={0}
            max={1440}
            step={15}
            value={1440 - minutesAgo}
            onChange={(e) => setMinutesAgo(1440 - Number(e.target.value))}
            className="min-w-[200px] flex-1"
            aria-valuetext={minutesAgo ? `${duration(minutesAgo * 60000)} ago` : "now"}
          />
          <span className="narrow w-24 text-sm" style={{ color: "var(--muted)" }}>
            {minutesAgo ? `${duration(minutesAgo * 60000)} ago` : "Now"}
          </span>
          <button className="btn" disabled={!minutesAgo} onClick={() => setMinutesAgo(0)}>
            Back to now
          </button>
        </div>
      )}
    </div>
  );
}

function SelectedPanel({ sel, onClose }: { sel: NonNullable<Selected>; onClose: () => void }) {
  return (
    <section className="panel rounded-lg p-4" aria-live="polite">
      <div className="mb-2 flex items-start gap-2">
        <h2 className="m-0 flex-1 text-base font-bold">{sel.item.name}</h2>
        <button className="btn" onClick={onClose} aria-label="Close details">
          Close
        </button>
      </div>
      {sel.kind === "region" ? (
        <>
          <p className="m-0 text-sm">
            <span className={`st-${sel.item.state} inline-flex items-center gap-1.5`}>
              <Glyph state={sel.item.state} size={10} /> {STATE_LABEL[sel.item.state]}
            </span>
          </p>
          <ul className="mb-0 mt-2 grid gap-1 pl-5 text-sm">
            {sel.item.items.map((it, i) => (
              <li key={i}>
                <Link to={`/service/${it.service_id}`}>{it.service_name}</Link>: {it.what}
              </li>
            ))}
            {sel.item.items.length === 0 && <li style={{ color: "var(--muted)" }}>Nothing reported for this region.</li>}
          </ul>
        </>
      ) : (
        <>
          <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
            {sel.item.location ?? "Unknown location"}, {sel.item.fresh ? "reporting" : "not reporting"}
          </p>
          <p className="mb-1 mt-2 text-sm">
            {sel.item.ok_checks} checks passing{sel.item.median_ms ? `, median ${ms(sel.item.median_ms)}` : ""}
          </p>
          {sel.item.failing.length > 0 && (
            <ul className="m-0 grid gap-1 pl-5 text-sm" style={{ color: "var(--major)" }}>
              {sel.item.failing.map((f) => (
                <li key={f.service_id}>
                  <Link to={`/service/${f.service_id}`}>{f.service_name}</Link> unreachable from here
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function MapCanvas({ data, regions, onSelect, height }: { data: MapData | null; regions: MapRegion[]; onSelect: (s: Selected) => void; height: string }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const markers = useRef<{ m: maplibregl.Marker; root: Root }[]>([]);
  const [ready, setReady] = useState(false);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);
  const styleUrl = data?.style_url ?? null;
  const dataLoaded = data !== null;
  const latest = useRef({ regions, onSelect });
  latest.current = { regions, onSelect };

  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(document.documentElement.dataset.theme));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!el.current || !dataLoaded) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: styleUrl ?? baseStyle(),
      center: [10, 25],
      zoom: 1.1,
      attributionControl: styleUrl ? undefined : { customAttribution: "Natural Earth" },
      renderWorldCopies: false,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => {
      m.addSource("regions", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: "regions",
        type: "circle",
        source: "regions",
        filter: ["==", ["get", "state"], "operational"],
        paint: { "circle-radius": 3.5, "circle-color": stateColorExpr(), "circle-opacity": 0.75, "circle-stroke-width": 1, "circle-stroke-color": cssVar("--bg") },
      });
      m.on("click", "regions", (e) => {
        const id = e.features?.[0]?.properties?.id;
        const r = latest.current.regions.find((x) => x.id === id);
        if (r) latest.current.onSelect({ kind: "region", item: r });
      });
      m.on("mouseenter", "regions", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "regions", () => (m.getCanvas().style.cursor = ""));
      setReady(true);
    });
    map.current = m;
    return () => {
      markers.current.forEach(({ m: mk, root }) => {
        mk.remove();
        window.setTimeout(() => root.unmount());
      });
      markers.current = [];
      m.remove();
      map.current = null;
      setReady(false);
    };
  }, [styleUrl, theme, dataLoaded]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !data) return;
    (m.getSource("regions") as GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: regions.map((r) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [r.lon, r.lat] },
        properties: { id: r.id, state: r.state, name: r.name },
      })),
    });
    markers.current.forEach(({ m: mk, root }) => {
      mk.remove();
      window.setTimeout(() => root.unmount());
    });
    markers.current = [];
    const add = (lon: number, lat: number, node: React.ReactNode, label: string, onClick?: () => void, anchor: maplibregl.PositionAnchor = "center") => {
      const div = document.createElement("button");
      div.type = "button";
      div.setAttribute("aria-label", label);
      div.style.cssText = "background:none;border:0;padding:0;cursor:pointer";
      if (onClick)
        div.addEventListener("click", (e) => {
          // Don't let the map also handle this click (it would select a dot underneath).
          e.stopPropagation();
          onClick();
        });
      const root = createRoot(div);
      root.render(node);
      const mk = new maplibregl.Marker({ element: div, anchor, offset: anchor === "bottom" ? [0, -4] : anchor === "top" ? [0, 4] : [0, 0] })
        .setLngLat([lon, lat])
        .addTo(m);
      // MapLibre replaces the label with a generic "Map marker"; restore ours for screen readers.
      mk.getElement().setAttribute("aria-label", label);
      if (!onClick) mk.getElement().setAttribute("role", "img");
      markers.current.push({ m: mk, root });
    };
    for (const a of data.agents) {
      const st: State = !a.fresh ? "unknown" : a.failing.length ? "major_outage" : "operational";
      add(
        a.lon,
        a.lat,
        <span className={`st-${st} narrow inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold`} style={{ background: "var(--bg)", border: "1.5px solid var(--st)", color: "var(--text)" }}>
          <Glyph state={st} size={9} />
          {a.name}
        </span>,
        `Agent ${a.name}`,
        () => onSelect({ kind: "agent", item: a }),
        "bottom",
      );
    }
    if (data.host) {
      add(
        data.host.lon,
        data.host.lat,
        <span className="narrow rounded px-2 py-0.5 text-xs font-bold" style={{ background: "var(--text)", color: "var(--bg)" }}>
          You
        </span>,
        "This Pulse",
        undefined,
        "top",
      );
    }
    // Regions last (worst on top) so their glyphs stay clickable under agent labels.
    const ranked = regions.filter((x) => x.state !== "operational").sort((a, b) => STATE_RANK[a.state] - STATE_RANK[b.state]);
    for (const r of ranked) {
      add(r.lon, r.lat, <Glyph state={r.state} size={isProblem(r.state) ? 18 : 14} />, `${r.name}: ${STATE_LABEL[r.state]}`, () => onSelect({ kind: "region", item: r }));
    }
  }, [data, regions, ready, onSelect]);

  return <div ref={el} style={{ height, width: "100%" }} role="region" aria-label="Outage map" />;
}
