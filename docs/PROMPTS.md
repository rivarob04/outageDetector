# Claude Code prompt pack: self-hosted outage map

## How to use this

1. Create the repo and put `SPEC.md` at `docs/SPEC.md`.
2. Run **one phase per Claude Code session**, in order. Start each session in plan mode (Shift+Tab) so you can review the plan before Claude writes code.
3. At the end of each phase, have Claude commit and push to a branch, then review and merge. A clean context for each phase keeps quality high.
4. Phases 6–9 can be reordered or skipped. Phases 0–5 give you a usable product.

---

## Phase 0: Kickoff & architecture (no code yet)

```
Read docs/SPEC.md carefully. We're building a self-hosted outage map / down detector deployed via docker compose.

Do NOT write application code in this session. Instead:

1. Challenge the spec. List gaps, contradictions, risky assumptions and anything you'd cut from v1. If you would change the default tech stack, say why. Otherwise confirm it.
2. Write CLAUDE.md at the repo root. It's our working agreement: stack, commands (dev, test, lint, build, compose up), code style, the rule that no unit test touches the network, "verify endpoints live before adding them to the catalog", "update docs and CHANGELOG with every change", and conventional commits.
3. Write docs/ARCHITECTURE.md: components, data model (tables and key fields), the scheduler design, the adapter interface, the probe interface, the verdict engine inputs and outputs, the SSE event flow, the agent push protocol (auth, payload, replay protection), and retention/rollups. Include a mermaid diagram.
4. Write docs/ROADMAP.md that maps the phases (1–9 below) to concrete milestones with acceptance criteria.

Phases for reference: 1 skeleton + compose + CI, 2 provider adapters + catalog, 3 local probes + canaries + verdict engine, 4 storage/history/API/SSE, 5 dashboard UI, 6 map + vantage agents, 7 alerts + integrations, 8 advanced signals (dependency correlation, anomaly detection, community chatter), 9 hardening, docs and release.

Ask me any blocking questions at the end. Commit on a branch named phase-0.
```

## Phase 1: Skeleton, Docker, CI

```
Implement Phase 1 from docs/ROADMAP.md, following CLAUDE.md and docs/ARCHITECTURE.md.

Deliver:
- The backend skeleton (FastAPI) with settings from env vars, structured JSON logging, /healthz and /readyz, and Alembic set up with an initial migration.
- A frontend skeleton (Vite + React + TS + Tailwind) with a placeholder page, built into static files served by the backend.
- One multi-stage Dockerfile (non-root user, a HEALTHCHECK, small final image, amd64 and arm64).
- deploy/docker-compose.yml with a named volume for data, .env.example, restart policy and healthcheck, and optional profiles `postgres`, `mqtt` and `agent`, stubbed for now.
- A Makefile or justfile: dev, test, lint, fmt, build, up, down, logs.
- GitHub Actions: lint, typecheck and tests on PRs; a compose smoke test (bring the stack up, curl /healthz); a multi-arch build and push to GHCR on tags.
- A pre-commit config.

Done when `docker compose -f deploy/docker-compose.yml up -d --build` works from a clean clone and /healthz returns 200. Show me the image size and idle RAM usage. Commit on the phase-1 branch.
```

## Phase 2: Provider feed adapters & catalog

```
Implement Phase 2: official status-feed adapters and the service catalog.

1. Define the adapter interface from ARCHITECTURE.md. Each adapter normalizes into Service → Components → Incidents / Maintenances with a normalized severity. Support ETag/If-Modified-Since, timeouts, retries with backoff, per-host rate limits, jitter and a descriptive User-Agent ("Pulse/<version> (+repo url)").
2. Implement adapters in this order: Atlassian Statuspage, incident.io status pages, AWS Health, Google Cloud, Google Workspace, Azure RSS, Slack, generic RSS/Atom. Add Instatus / Better Stack if any catalog service needs them.
3. Build catalog/*.yaml, one file per provider, validated by a JSON Schema / Pydantic model. Start with: GitHub, GitLab, Cloudflare, AWS, Google Cloud, Google Workspace, Azure, Microsoft 365, Anthropic (Claude), OpenAI, Slack, Discord, Zoom, Atlassian (Jira/Confluence), Vercel, Netlify, DigitalOcean, Fastly, Akamai, Docker Hub, npm, PyPI, Stripe, Twilio, Dropbox, Reddit, Steam, PlayStation Network, Xbox Live, Netflix, Spotify, Apple (iCloud/System Status), Tailscale, Home Assistant Cloud, Plex.
4. IMPORTANT: verify every feed URL live before committing it. Don't trust memory, because many providers have moved platforms. Record the results in docs/catalog-verification.md (service, adapter, URL, HTTP status, date checked, notes). If a service has no official machine-readable feed, mark it "checks only" and leave the feed out.
5. Map components to regions where possible (AWS/GCP/Azure regions, Cloudflare data centers parsed from component names), with lat/lon in a regions table, for the map in Phase 6.
6. Record real responses as test fixtures (sanitized) and write adapter contract tests against them. No live network in tests.
7. Add a CLI command `pulse catalog verify` that re-checks every catalog endpoint and prints a table. Users and CI (weekly scheduled job) can both run it.

Commit on the phase-2 branch, with the verification table in the PR description.
```

## Phase 3: Local probes, canaries & verdict engine

```
Implement Phase 3: synthetic probes from this host and the verdict engine.

Probes (async, concurrent, bounded by a semaphore):
- HTTP(S) with timing breakdown (DNS, connect, TLS, TTFB, total), expected status codes, an optional keyword assertion, redirect handling and an IPv4/IPv6 preference.
- DNS resolution through the system resolver plus configurable public resolvers (default 1.1.1.1, 8.8.8.8, 9.9.9.9), so we can classify "DNS failure" separately.
- TCP connect, TLS certificate expiry (warn below 14 days for custom services), and optional ICMP (works without NET_RAW; document the capability if enabled).
- Local canaries: default gateway plus 2–3 highly available endpoints. If the majority fail, set the global flag local_network=true.

Latency baselines: rolling p50/p95 per probe over 24 h and 7 d. "Degraded" means latency stays above k × p95 baseline for N consecutive checks (configurable, with sensible defaults).

Verdict engine:
- Implement exactly the states, scopes, flap protection and hysteresis in SPEC §4. Write docs/verdict.md as the source of truth, with a decision table.
- It must be a pure function: (feed state, local probe results, vantage results, canary state, config) → (state, scope, reasons[]). reasons[] holds human-readable strings that the UI shows verbatim.
- Write exhaustive table-driven unit tests, including: provider says down but probes pass (trust the provider: partial); probes fail with no feed incident (local_only, or widespread if vantage agrees); canaries down (unknown/local_network); maintenance windows; flapping.

Also add SSRF protection for custom probes (block private/link-local/metadata ranges unless ALLOW_PRIVATE_TARGETS=true) and per-service intervals (default: feeds 60 s, probes 60 s, with jitter).

Commit on the phase-3 branch.
```

## Phase 4: Storage, history, API, live updates

```
Implement Phase 4: persistence and API.

- Tables per ARCHITECTURE.md: services, tracked services, components, incidents and incident updates (deduplicated by provider ID), probe results, rollups, state transitions, and settings.
- Retention and rollup jobs: raw probe results for 7 d, 5-minute rollups for 90 d, daily rollups forever (all configurable). Make sure SQLite stays small. Show me the expected DB size for 40 services at 60 s intervals after 1 year.
- REST API (versioned under /api/v1) with OpenAPI docs: catalog, tracked services CRUD, service detail, incidents (filterable), uptime per period, latency series, global connectivity status, settings, and config import/export.
- Server-Sent Events endpoint /api/v1/events for state changes and new incident updates.
- Auth: an admin account (created on first run via a setup screen or an env var), session cookies, CSRF protection and a login rate limit. A setting for public read-only vs private viewing. Optional trusted-header auth (Authelia/Authentik/Cloudflare Access) behind an env flag.
- Optional config-as-code: if config/pulse.yaml exists, it seeds or overrides tracked services. Document the precedence.

Tests for the API (httpx test client) and the migrations. Commit on the phase-4 branch.
```

## Phase 5: Dashboard UI

```
Implement Phase 5: the main dashboard. Make it look like a polished product, not a template: a distinct visual identity, tight typography, and clear status colors that remain readable for colorblind users (pair color with icons/shapes).

Views:
1. Header "Is it me?" strip: your connectivity, DNS health, an active incident count and last-updated time.
2. Active incident banner(s) with scope badges (Provider confirmed / Widespread / Only from your network / You're offline).
3. A grid of service tiles: logo, name, state, scope badge, 24 h latency sparkline and a 90-day uptime bar. Category filter chips, search, sort (status first / A–Z / category) and a compact/comfortable density toggle.
4. Service detail: components list, an incident timeline with provider update text, latency chart with stacked DNS/connect/TLS/TTFB, uptime stats, upcoming maintenance and links to the official status page. A "Why is this red?" panel renders reasons[] from the verdict engine.
5. Catalog browser ("+ Add service") with categories and a custom URL service form (assertions, interval, headers).
6. Incidents history page with filters and MTTR per provider.
7. Settings: themes, alert channels (stub until Phase 7), canaries, resolvers, retention, auth/public mode, import/export.

Also: live updates via SSE (no polling), light/dark/system/high-contrast themes, 1- or 2-column mobile layout, PWA manifest, keyboard shortcuts ("/" to search, "g i" for incidents), and TV/kiosk mode (?kiosk=1: large type, auto-rotates active incidents, then the grid, then the map).

Add demo mode (PULSE_DEMO=1) with realistic fake data covering every state and scope, and use it to capture screenshots into docs/screenshots/. Add a Playwright smoke test that loads the dashboard in demo mode. Commit on the phase-5 branch.
```

## Phase 6: Outage map & remote vantage agents

```
Implement Phase 6: the map and vantage agents.

Map:
- MapLibre GL with a free, keyless tile source (or bundled low-zoom vector tiles for offline use). Document the attribution requirements.
- Layers: provider regions (colored by component state), vantage agents (colored by their probe results), and your own host.
- Click a region to see the affected services/components. Click an agent to see its results and latency to each service.
- A 24 h time scrubber that replays state from rollups.
- A layer filter per provider (e.g. show only AWS regions).

Vantage agents:
- Same image, `pulse agent` entrypoint. Config: HUB_URL, AGENT_TOKEN, AGENT_NAME, AGENT_LOCATION (city or lat/lon).
- Agents pull their probe list from the hub and push batched results over HTTPS. HMAC-signed payloads with a timestamp and nonce for replay protection. Outbound-only: no inbound ports on the agent.
- Admin UI to create/revoke agent tokens, see last-seen times and mark agents stale. A stale agent is excluded from verdicts.
- Feed agent results into the verdict engine for the `widespread` vs `local_only` scope, and extend the verdict tests.
- deploy/docker-compose.agent.yml: a minimal compose file someone can run on a VPS or a friend's Pi.

Commit on the phase-6 branch.
```

## Phase 7: Alerts & integrations

```
Implement Phase 7: notifications and outbound integrations.

Alerts:
- Apprise for delivery (ntfy, Discord, Slack, Telegram, Pushover, Gotify, email, Matrix…) plus generic webhooks signed with HMAC.
- Rules: per service or category, minimum severity, scope filter (provider_confirmed only / include widespread / include local_only), quiet hours with a timezone, debounce/cooldown, recovery notifications and a daily digest.
- Pause while local_network=true, then send a single "you were offline from X to Y" summary.
- A "Send test" button for every channel. Rich formatting where the channel supports it.

Integrations:
- Prometheus /metrics (optional token auth) and an example Grafana dashboard JSON in deploy/.
- MQTT with Home Assistant discovery: one device per service with status, scope, latency and incident-count entities. The compose `mqtt` profile includes Mosquitto. Document the HA setup.
- An RSS/Atom feed of incidents, SVG badges (/badge/{service}.svg), and an iCal feed of scheduled maintenance.
- An optional public status page at /status showing only the services you choose, with its own read-only access and no admin data.

Commit on the phase-7 branch.
```

## Phase 8: Advanced signals

```
Implement Phase 8: advanced signals. Each is behind a feature flag, and each extends the verdict tests.

1. Upstream correlation: use the catalog's depends_on (e.g. Vercel → AWS us-east-1, Cloudflare). When an upstream is in major_outage, dependents still green get an advisory "possibly affected via …". It's an advisory only and doesn't change their state. Show it on the tiles and the map, and populate depends_on for the existing catalog where public evidence supports it (note the sources in the YAML).
2. Anomaly detection: replace the fixed k × p95 with a robust method (e.g. EWMA plus MAD) per probe and time-of-day bucket. Keep it explainable, so reasons[] says something like "latency 4.2× normal for this hour".
3. Community chatter (off by default): poll ToS-friendly public sources (e.g. Hacker News Algolia search API, Reddit public JSON, Bluesky public search) for "<service> down"/"outage" mentions. Use spike detection against a baseline. It shows only as a weak "chatter spike" chip and never changes state alone. Respect rate limits; no scraping of Downdetector or similar sites.
4. "Before they admit it" metric: track the time between our widespread detection and the provider's first incident post. Show it per provider on the history page.

Commit on the phase-8 branch.
```

## Phase 9: Hardening, docs, release

```
Implement Phase 9: get the project ready for others to self-host.

- A security pass: dependency audit, container scan (Trivy in CI), read-only root FS, dropped capabilities, no secrets in logs, and a CSP header for the UI. Review SSRF, auth and the agent protocol, and write docs/SECURITY.md with a threat model.
- Performance: test with 150 tracked services and 5 agents on a Raspberry Pi-class limit (`cpus: 1`, `mem_limit: 256m`). Report CPU/RAM and fix hot spots.
- Backups: a `pulse backup` / `pulse restore` CLI and documentation for volume backups.
- Upgrades: migrations run automatically on start, with a documented rollback.
- Docs: a README with screenshots and a 60-second quickstart; reverse proxy examples (Traefik labels, Caddy, Nginx Proxy Manager, Cloudflare Tunnel); configuration reference (every env var); "adding a provider" guide; FAQ ("why is it yellow?").
- CONTRIBUTING.md and issue templates, including a "request a service" template that asks for the status page URL.
- Release workflow: semantic version tags, GHCR images, a CHANGELOG and GitHub release notes.

Finally, do a fresh-clone run-through exactly as a new user would, following only the README, and fix anything that breaks. Commit on the phase-9 branch.
```

---

## Reusable prompts (use any time)

### Add a new service
```
Add <SERVICE NAME> to the catalog. Find its official status page and determine which adapter fits. Verify the feed and probe URLs live, add the YAML with category, icon, region mapping and depends_on where evidence supports it, record a sanitized fixture, add a contract test, and update docs/catalog-verification.md. If there's no machine-readable feed, make it checks-only and explain why.
```

### Review pass (run after any phase)
```
Act as a skeptical senior reviewer of this branch versus main. Check: correctness of the verdict logic, error handling around network failures, the no-network-in-tests rule, security (SSRF, auth, secrets), and docs drift (does the README still match reality?). List findings ranked by severity, fix everything that's clearly a bug, and ask me about anything that's a judgment call.
```

### Bug report
```
Bug: <what happened> / Expected: <what should happen> / Steps: <how to reproduce>.
Reproduce it with a failing test first, then fix it, then confirm the test passes and that no other tests broke.
```

### Catalog health check (monthly)
```
Run `pulse catalog verify`. For every failing or redirected endpoint, find out whether the provider moved status platforms, update the catalog and fixtures, and summarize the changes.
```
