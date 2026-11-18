# Roadmap

Each phase is a branch (`phase-N`) stacked on the previous one. "Done" always includes the definition of done in SPEC §8.

| Phase | Milestone | Acceptance criteria |
|---|---|---|
| 0 | Plan | SPEC, SPEC-REVIEW, CLAUDE.md, ARCHITECTURE and ROADMAP are committed. |
| 1 | Skeleton | `docker compose up` gives a healthy container; `/healthz` returns 200 and `/readyz` checks the DB; the UI placeholder is served; Alembic initial migration; Makefile; CI (lint, typecheck, tests, compose smoke test, multi-arch GHCR on tags); pre-commit. |
| 2 | Feeds + catalog | Statuspage, incident.io, AWS, GCP, Google Workspace, Azure, Slack and RSS adapters with fixture contract tests; ≥35 catalog entries validated by schema; `pulse catalog verify`; the `catalog-verify` workflow produces `docs/catalog-verification.md`; regions file with coordinates. |
| 3 | Probes + verdict | HTTP/DNS/TCP/TLS probes with timing breakdown; canaries flip `local_network`; latency baselines; pure verdict engine with a decision table in `docs/verdict.md` and ≥25 table-driven tests; SSRF guard; scheduler with jitter. |
| 4 | Persistence + API | All tables and migrations; rollups and retention; `/api/v1` with OpenAPI; SSE; admin auth (setup screen and env bootstrap), CSRF and login rate limit; trusted-header auth; config-as-code with documented precedence; API tests; DB size estimate in the docs. |
| 5 | Dashboard | Header strip, banners, tile grid with sparkline and uptime bar, filters and search, service detail with a "Why is this red?" panel, catalog browser and custom service form, incident history and MTTR, settings, themes (including high contrast), mobile 1/2 columns, PWA, keyboard shortcuts, kiosk mode, demo mode, screenshots, Playwright smoke test. |
| 6 | Map + agents | MapLibre map with an offline basemap; region, agent and host layers; click-through; 24 h scrubber; provider filter; `pulse agent`; signed push with replay protection; agent admin UI; stale handling; agent results feed the verdicts; `docker-compose.agent.yml`. |
| 7 | Alerts + integrations | Apprise and HMAC webhooks; rules (scope, severity, quiet hours, cooldown, recovery, digest); offline pause and summary; test button; `/metrics` plus a Grafana JSON; MQTT HA discovery plus a Mosquitto profile; RSS/Atom, iCal and badges; public `/status` page. |
| 8 | Advanced signals | `depends_on` advisories; EWMA+MAD anomaly detection by time-of-day bucket; chatter spikes (feature flag, off by default); detection lead-time metric. |
| 9 | Release | Trivy in CI, CSP, docs/SECURITY.md threat model; a 150-service perf test with measurements; `pulse backup`/`restore`; automatic migrations; full README, configuration reference, reverse-proxy examples, provider guide, FAQ; CONTRIBUTING and issue templates; release workflow; fresh-clone run-through. |
