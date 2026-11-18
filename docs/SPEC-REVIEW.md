# Spec review (Phase 0)

This is a critical pass over `docs/SPEC.md` before writing code. The decisions here are binding for v1 unless a later phase changes them and says so in the CHANGELOG.

## Gaps and contradictions found

| # | Issue | Decision |
|---|---|---|
| 1 | §2.2 says to set everything to `unknown` when offline, but the official feed is a separate signal. If the last feed fetch is fresh, it is still true. | When `local_network` is true, local probe results are ignored. A service whose feed data is still fresh (< 3 × poll interval old) keeps its feed-derived state. Everything else becomes `unknown` with scope `local_network`. |
| 2 | The spec doesn't define the state of a `local_only` service. Red is alarming, but it is down *for you*. | The state reflects what you experience (e.g. `major_outage`) and the scope is `local_only`. The UI uses a muted variant of the color plus a "Only from your network" badge. Alerts filter by scope. |
| 3 | The spec gives agent auth as an "HMAC-signed per-agent token" but doesn't say where the hub keeps the secret. | Per-agent secrets are derived: `secret = HMAC(SECRET_KEY, agent_id)`. Only the agent ID and a revoked flag are stored. Rotating `SECRET_KEY` invalidates every agent, which is documented as the "revoke all" switch. |
| 4 | "Public IP/ISP" in the header requires calling a third-party service, which conflicts with "zero telemetry". | Off by default. When enabled, it uses a configurable URL (default `https://api.ipify.org`). |
| 5 | Offline-capable map tiles vs. a keyless tile source. | The default basemap is bundled: Natural Earth 110m countries (public domain) via the `world-atlas` npm package, so it works offline. An optional keyless OpenFreeMap style can be set with `PULSE_MAP_STYLE_URL`. |
| 6 | ICMP needs `NET_RAW`, which conflicts with "dropped capabilities". | ICMP is cut from v1. TCP connect gives the same reachability signal without extra privileges. |
| 7 | "Verify every endpoint live" vs. building in an environment that can't reach status pages. | Verification runs in GitHub Actions (`catalog-verify.yml`). It writes `docs/catalog-verification.md` and refreshes the recorded fixtures. Entries the workflow can't verify are marked `verified: false` and are **not** shown in the UI catalog by default. |
| 8 | Postgres is "optional" but untested claims drift. | SQLAlchemy Core is used with portable SQL only. CI runs the test suite against SQLite and Postgres. TimescaleDB is cut from v1 because plain Postgres works. |
| 9 | Community chatter depends on third-party APIs that change often. | It stays a feature flag that is off by default. Each source is isolated so its failure never affects the verdicts. |

## Cut from v1 (can come back later)
- ICMP probes (see #6).
- TimescaleDB-specific features.
- A Downdetector-style crowd-reporting button. It needs public exposure and abuse protection; maybe later.
- Multi-user / RBAC. v1 has one admin plus optional public read-only access.

## Stack decision
The default stack is kept: **Python 3.12 / FastAPI / SQLAlchemy Core (async) / SQLite (WAL)** for the backend, and **React + Vite + TS + Tailwind** for the frontend. Reasons:
- The core work is async I/O-bound polling with simple maths, which suits asyncio and httpx well.
- `apprise` (alerts) and `paho-mqtt` are Python-native, so no bridging is needed.
- One container serves the API and the static UI, with a measured idle RSS target below 150 MB.

Go would give a smaller image. That's not worth losing Apprise and slowing iteration.

## Product name
The working name is **Pulse** (CLI `pulse`, env prefix `PULSE_`). The repo name `outageDetector` is unchanged. Renaming means a global search/replace of `Pulse`/`pulse`/`PULSE_`.
