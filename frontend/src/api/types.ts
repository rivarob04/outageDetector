export type State = "operational" | "degraded" | "partial_outage" | "major_outage" | "maintenance" | "unknown";
export type Scope = "provider_confirmed" | "widespread" | "local_only" | "local_network" | "none";

export interface UptimeDay {
  day: string;
  pct: number | null;
  incident: boolean;
}

export interface TileIncident {
  id: number;
  title: string;
  impact: string;
  kind: "incident" | "maintenance";
  url: string | null;
  started_at: string | null;
}

export interface ServiceBase {
  id: string;
  name: string;
  category: string;
  category_label: string;
  icon: string | null;
  homepage: string | null;
  status_page: string | null;
  source: "catalog" | "custom" | "config";
  signal: "feed" | "checks" | "feed+checks";
  verified: boolean | null;
  depends_on: string[];
}

export interface Verdict {
  state: State;
  scope: Scope;
  reasons: string[];
  advisories: string[];
  since: string | null;
  pending_state: State | null;
}

export interface ServiceSummary extends ServiceBase, Verdict {
  last_checked: string | null;
  sparkline: (number | null)[];
  uptime: UptimeDay[];
  uptime_pct: number | null;
  incidents: TileIncident[];
}

export interface Canary {
  target: string;
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface Connectivity {
  public_ip?: string | null;
  offline: boolean;
  offline_since: string | null;
  checked_at: string | null;
  canaries: Canary[];
}

export interface Summary {
  generated_at: string;
  version: string;
  demo: boolean;
  connectivity: Connectivity;
  counts: Partial<Record<State, number>>;
  categories: Record<string, string>;
  services: ServiceSummary[];
}

export interface IncidentUpdate {
  status: string;
  body: string;
  created_at: string | null;
}

export interface IncidentItem {
  id: number;
  service_id: string;
  service_name: string | null;
  kind: "incident" | "maintenance";
  title: string;
  impact: string;
  status: string;
  url: string | null;
  started_at: string | null;
  resolved_at: string | null;
  scheduled_for: string | null;
  scheduled_until: string | null;
  regions: string[];
  duration_s: number | null;
  updates?: IncidentUpdate[];
}

export interface ProbeLatest {
  ts: string;
  ok: boolean;
  latency_ms: number | null;
  dns_ms: number | null;
  connect_ms: number | null;
  tls_ms: number | null;
  ttfb_ms: number | null;
  status_code: number | null;
  error: string | null;
  detail: Record<string, unknown> | null;
}

export interface ProbeInfo {
  id: string;
  kind: "http" | "dns" | "tcp" | "tls";
  url: string | null;
  host: string | null;
  port: number | null;
  interval_s: number;
  latest: ProbeLatest | null;
}

export interface AgentResult {
  probe_id: string;
  ok: boolean;
  latency_ms: number | null;
  ts: string | null;
  consecutive_failures: number;
}

export interface ServiceDetail extends ServiceBase, Verdict {
  tracked: boolean;
  description: string | null;
  notes: string | null;
  config: Record<string, unknown> | null;
  last_checked: string | null;
  feed: {
    url: string | null;
    adapter: string | null;
    indicator: string | null;
    description: string | null;
    ok: boolean | null;
    error: string | null;
    fetched_at: string | null;
  } | null;
  components: { name: string; group: string | null; status: string; region: string | null }[];
  probes: ProbeInfo[];
  incidents: IncidentItem[];
  uptime: UptimeDay[];
  uptime_pct: number | null;
  agents: { id: string; name: string; location: string | null; last_seen_at: string | null; results: AgentResult[] }[];
  transitions: { from_state: State | null; to_state: State; scope: Scope; reasons: string[]; ts: string }[];
}

export interface LatencyPoint {
  ts: string;
  ok: boolean;
  latency_ms: number | null;
  dns_ms?: number | null;
  connect_ms?: number | null;
  tls_ms?: number | null;
  ttfb_ms?: number | null;
  p95_ms?: number | null;
  error?: string | null;
}

export interface LatencySeries {
  range: string;
  probes: { id: string; kind: string; target: string; points: LatencyPoint[] }[];
}

export interface CatalogItem extends ServiceBase {
  tracked: boolean;
  notes?: string | null;
}

export interface Me {
  user: { username: string; via: string } | null;
  csrf: string | null;
  setup_required: boolean;
  public_view: boolean;
  demo: boolean;
  version: string;
}

export interface ProviderStats {
  service_id: string;
  service_name: string;
  count: number;
  mttr_s: number | null;
  median_s: number | null;
  lead?: { count: number; median_lead_s: number | null; confirmed: number; unconfirmed: number };
}
