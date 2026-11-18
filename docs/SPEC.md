# Pulse — Self-Hosted Outage Map & Down Detector

> Commit this file to the repo as `docs/SPEC.md`. Every prompt refers back to it.
> "Pulse" is a placeholder name. Rename it freely.

## 1. Goal

A self-hosted dashboard that answers one question in under 2 seconds: **"Is it down, or is it me?"**

It watches popular cloud services and SaaS (GitHub, AWS, Cloudflare, Google, Anthropic, etc.) using three independent signal types, combines them into an honest verdict, shows them on a live map, and alerts you. It deploys with a single `docker compose up -d`.

## 2. Core principles

1. **Signals come with provenance.** The UI always says *why* something is red: "Provider confirmed", "Failing from your network only", "Failing from 3 of 4 vantage points", etc.
2. **Local problems never pose as global outages.** If your own internet or DNS is broken, the service states become `unknown` and alerts pause, rather than every tile turning red.
3. **Be a polite client.** Respect the poll intervals, add jitter, send conditional requests (ETag / If-Modified-Since) and a descriptive User-Agent. Never scrape sites whose terms forbid it (e.g. Downdetector).
4. **Zero telemetry.** Data goes out only as the HTTP requests needed for checks.
5. **Works on a Raspberry Pi.** It idles at under 150 MB of RAM and ships multi-arch images (amd64 and arm64).

## 3. Signal sources

### 3a. Official provider feeds ("Feed")
Each source type is a pluggable **adapter** that normalizes data into a common model: `Service → Components → Incidents/Maintenances`.

| Adapter | Examples | Notes |
|---|---|---|
| Atlassian Statuspage (`/api/v2/summary.json`, `/incidents.json`, `/scheduled-maintenances.json`) | GitHub, Cloudflare, Discord, Reddit, Atlassian, Dropbox, Twilio, DigitalOcean, Linode, Vercel, Netlify | Covers about 60% of services |
| incident.io status pages | Several AI/SaaS vendors | Verify the JSON endpoint per site |
| Instatus / Better Stack / Status.io / Sorry | Assorted | Add as needed |
| AWS Health (public RSS / JSON) | AWS per service and region | Region-aware, so it feeds the map |
| Google Cloud (`status.cloud.google.com/incidents.json`) | GCP | Region-aware |
| Google Workspace status dashboard JSON | Gmail, Drive, Meet | |
| Azure status RSS | Azure | |
| Slack status API | Slack | |
| Generic RSS/Atom | Fallback for anything else | Heuristic severity parsing |

**Every catalog endpoint must be verified live before it ships.** Record the result in `docs/catalog-verification.md` (service, adapter, URL, HTTP code, date). Don't guess URLs from memory.

### 3b. Synthetic probes from this host ("Checks")
- HTTP(S): status code, expected status/keyword, total latency, and a TTFB / DNS / connect / TLS breakdown.
- DNS: resolve each service hostname through several resolvers (the system resolver plus 1.1.1.1, 8.8.8.8 and 9.9.9.9). This separates "DNS is broken" from "the service is broken".
- TCP connect, TLS certificate expiry, and optional ICMP.
- **Local canaries:** 2–3 highly available targets, plus the gateway. If the canaries fail, the host is offline: set everything to `unknown` and pause alerts.
- **Latency baselines:** a rolling 24 h and 7 d p50/p95 per probe. When latency stays above k × baseline for N checks, the probe goes "degraded" even if it returns 200.

### 3c. Remote vantage agents ("Vantage")
- A tiny `pulse-agent` container (the same image with a different entrypoint) that friends or a cheap VPS can run.
- It pulls its probe list from the hub, runs the probes, and pushes results back over HTTPS, signed with a per-agent token. It never needs inbound ports.
- Each agent has a label and coordinates (or city/country), so the map can show the outage from several places.
- With no agents running, the app still works fully on local and feed signals.

### 3d. Community signals (optional, off by default)
- Keyword-spike detection on public, ToS-friendly sources (e.g. Hacker News Algolia API, Reddit public JSON, Bluesky search) for "<service> down".
- Shown as a weak signal ("chatter spike") that never sets a red status by itself.

## 4. Verdict engine

Per service, compute one state: `operational | degraded | partial_outage | major_outage | maintenance | unknown`, plus a **scope**:

| Scope | Meaning |
|---|---|
| `provider_confirmed` | The official feed reports an incident |
| `widespread` | Probes fail from ≥2 vantage points, or ≥50% of them, with no feed incident yet ("before they admit it") |
| `local_only` | Fails from this host only, while vantage agents and the feed are fine |
| `local_network` | The canaries fail, so nothing can be concluded |

Rules are deterministic, documented in `docs/verdict.md`, and unit tested with table-driven cases. Use flap protection: a state must persist for N consecutive checks before changing (configurable), and recovery uses hysteresis.

**Upstream correlation:** a dependency map (`depends_on: [aws:us-east-1, cloudflare]`) in the catalog. When an upstream has a major outage, dependents still green show "possibly affected via AWS us-east-1".

## 5. Features

### Dashboard
- A grid of service tiles: status color, scope badge, a 24 h latency sparkline and a 90-day uptime bar.
- Category filters (Cloud, Dev tools, AI, Comms, Streaming, Gaming, Smart home, Finance, My services) and search.
- Top banner for active incidents. An "Is it me?" header shows your connectivity, public IP/ISP (optional) and DNS health.
- Service detail page: components, an incident timeline with provider updates, latency charts with DNS/connect/TLS/TTFB breakdown, per-vantage results, and upcoming maintenance.
- **Map view** (MapLibre GL with a free tile source; no API key required):
  - Provider regions (AWS, GCP, Azure regions; Cloudflare data centers from the Statuspage component names) as colored dots.
  - Vantage agents as markers, each with its own results.
  - A "time scrubber" that replays the last 24 h.
- History: a filterable incident log, MTTR per provider and monthly uptime %.
- Themes: light, dark, follow-system, and a high-contrast mode. **TV/kiosk mode** auto-cycles views and stays legible from across a room. Installable as a PWA. Mobile layout in 1 or 2 columns.
- **Demo mode** (`PULSE_DEMO=1`) with fake data, for screenshots and UI development.

### Catalog & configuration
- Built-in `catalog/*.yaml`: one file per provider with id, name, category, icon, adapter, feed URL, probe URLs, component-to-region mapping, depends_on and docs links.
- The user tracks services from the catalog in the UI. Nothing is polled until it's tracked.
- **Custom services**: any URL, homelab services included, with optional keyword/status assertions.
- Optional config-as-code: `config/pulse.yaml` overrides UI state, for GitOps users.
- Import/export of the config as JSON.

### Alerts
- Via **Apprise**, which covers ntfy, Gotify, Discord, Slack, Telegram, Pushover, email, Matrix and 80+ others. Generic signed webhooks too.
- Per-service subscription, a minimum severity, "only provider-confirmed" or "include widespread" filters, quiet hours, debouncing, a recovery message and a daily digest.
- Alerts pause while `local_network` is true, with a single "you appear to be offline" message once connectivity returns.

### Integrations / outputs
- REST API (OpenAPI docs at `/api/docs`) and server-sent events for live UI updates.
- Prometheus `/metrics` (state, latency, uptime per service and probe).
- An **MQTT with Home Assistant discovery** option, so every service shows up as HA entities automatically. This is the bridge to the HA world from the example project.
- An RSS/Atom feed of incidents, SVG status badges (`/badge/github.svg`) and an iCal feed of scheduled maintenance.
- An optional public read-only status page (selected services only), so you can share "is it down?" with family or your team.

### Security
- Admin login is required for changes. Viewing can be public or private (setting). There's an optional OIDC/forward-auth header mode for Authelia, Authentik or Cloudflare Access.
- SSRF guard: custom probes to private IP ranges are blocked unless `ALLOW_PRIVATE_TARGETS=true`, because homelab users will want to enable it.
- The container runs as non-root with a read-only root filesystem where possible. Secrets come from env vars or Docker secrets.

## 6. Default tech stack (Claude may propose alternatives, with reasons, in Phase 0)

- **Backend:** Python 3.12, FastAPI, httpx (async), APScheduler or a custom async scheduler, SQLAlchemy plus Alembic migrations.
- **DB:** SQLite in WAL mode by default. Postgres/TimescaleDB is optional via `DATABASE_URL`. Use downsampling/retention jobs: raw probe results for 7 d, 5-minute rollups for 90 d, daily rollups forever.
- **Frontend:** React + Vite + TypeScript + Tailwind, MapLibre GL and a light chart library. Built into static files that the backend serves (one container).
- **Images:** one multi-stage Dockerfile, multi-arch (amd64 and arm64), published to GHCR by GitHub Actions.
- **Compose:** `pulse` (app), plus optional profiles `mqtt` (Mosquitto), `postgres` and `agent`.

## 7. Repo layout (target)

```
/backend        app, adapters/, probes/, verdict/, notify/, api/
/frontend       React app
/catalog        provider YAMLs
/docs           SPEC.md, ARCHITECTURE.md, verdict.md, catalog-verification.md, screenshots/
/deploy         docker-compose.yml, compose examples (Traefik, Caddy, Cloudflare Tunnel), .env.example
/tests          unit, adapter contract tests with recorded fixtures, e2e
CLAUDE.md       working agreement for Claude
```

## 8. Definition of done (every phase)
- `docker compose up -d --build` works from a clean clone, and the healthcheck passes.
- Lint, typecheck and tests pass in CI.
- The docs and the CHANGELOG are updated, and nothing in the README is unverified.
- No network calls in unit tests (fixtures only).
