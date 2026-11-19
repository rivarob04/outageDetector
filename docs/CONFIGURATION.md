# Configuration reference

Every setting is an environment variable with the `PULSE_` prefix. Secrets can also be read from a file: set `PULSE_<NAME>_FILE=/run/secrets/...` (Docker secrets) for `SECRET_KEY`, `ADMIN_PASSWORD`, `MQTT_PASSWORD`, `METRICS_TOKEN` and `AGENT_TOKEN`.

Settings marked **UI** can also be changed in **Settings → Monitoring** or in `pulse.yaml`. Precedence, highest first: `pulse.yaml` › UI › environment › default.

## Core
| Variable | Default | Description |
|---|---|---|
| `PULSE_DATA_DIR` | `/data` | Where the SQLite database, the generated secret key and `pulse.yaml` live. |
| `PULSE_DATABASE_URL` | *(SQLite in the data dir)* | Use Postgres instead: `postgresql://user:pass@host:5432/db` (`postgres://` also works). |
| `PULSE_HOST` | `0.0.0.0` | Listen address. |
| `PULSE_PORT` | `8080` | Port Pulse listens on inside the container. |
| `PULSE_BASE_URL` | | Public URL of this instance, used for links in alerts, feeds and agent setup. |
| `PULSE_SECRET_KEY` | *(generated, saved in the data dir)* | Signs sessions and derives agent secrets. Rotating it signs everyone out and invalidates all agent tokens. |
| `PULSE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `PULSE_LOG_JSON` | `true` | JSON log lines (`false` for plain text). |
| `PULSE_DEMO` | `false` | Fill Pulse with realistic fake data and make no network calls. |
| `PULSE_STATIC_DIR` | *(bundled UI)* | Serve the UI from another directory. |
| `PULSE_CATALOG_DIR` | *(bundled catalog)* | Use your own catalog directory. |
| `PULSE_CONFIG_FILE` | `<data dir>/pulse.yaml` | Config-as-code file (see the README). |

## Access
| Variable | Default | Description |
|---|---|---|
| `PULSE_PUBLIC_VIEW` | `true` | **UI.** Anyone who can reach Pulse can view the dashboard. When `false`, viewing requires signing in. |
| `PULSE_ADMIN_USERNAME` / `PULSE_ADMIN_PASSWORD` | | Create the admin on first start. Otherwise use the setup code printed in the logs. |
| `PULSE_TRUSTED_AUTH_HEADER` | | Trust a username header from your auth proxy (Authelia/Authentik: `Remote-User`; Cloudflare Access: `Cf-Access-Authenticated-User-Email`). |
| `PULSE_TRUSTED_PROXIES` | `127.0.0.1/32,::1/128,172.16.0.0/12` | Only connections from these networks may set that header. Make sure Pulse's port is reachable **only** through the proxy. |
| `PULSE_COOKIE_SECURE` | `false` | Mark the session cookie `Secure` and send HSTS. Set `true` behind HTTPS. |

## Checks and polling
| Variable | Default | Description |
|---|---|---|
| `PULSE_FEED_INTERVAL_S` | `60` | **UI.** Status feed poll interval (seconds). |
| `PULSE_PROBE_INTERVAL_S` | `60` | **UI.** Default check interval. |
| `PULSE_CANARY_INTERVAL_S` | `30` | Connectivity canary interval. |
| `PULSE_CANARY_TARGETS` | `https://1.1.1.1,https://dns.google,https://www.msftconnecttest.com/connecttest.txt` | **UI.** When most of these fail, Pulse decides it's offline. |
| `PULSE_DNS_RESOLVERS` | `1.1.1.1,8.8.8.8,9.9.9.9` | **UI.** Public resolvers that a failed DNS check is compared against. |
| `PULSE_ALLOW_PRIVATE_TARGETS` | `false` | **UI.** Allow your own checks to private/LAN addresses (homelab). |
| `PULSE_POSTAL_CODE` | | **UI.** Your Canadian postal code (e.g. `N8W 1H4`). TekSavvy and Bell (via TekSavvy's outage tracker) then only count outages in your area (the first three characters, e.g. `N8W`) instead of anywhere. The code never leaves Pulse. |
| `PULSE_MAX_CONCURRENCY` | `20` | Maximum checks running at once. |
| `PULSE_HTTP_TIMEOUT_S` | `10` | Default request timeout. |
| `PULSE_USER_AGENT` | `Pulse/<version> (+repo URL)` | User-Agent sent to providers. |
| `PULSE_PUBLIC_IP_LOOKUP_URL` | *(off)* | Show your public IP in the header, e.g. `https://api.ipify.org?format=json`. Off by default because it contacts a third party. |

## Verdicts
| Variable | Default | Description |
|---|---|---|
| `PULSE_FAIL_THRESHOLD` | `2` | **UI.** Consecutive failures before a check counts as failing. |
| `PULSE_RECOVER_THRESHOLD` | `3` | **UI.** Evaluations a recovery must hold before switching back to Operational. |
| `PULSE_LATENCY_FACTOR` | `3.0` | **UI.** "Slow" means latency ≥ this multiple of normal. |
| `PULSE_LATENCY_MIN_MS` | `300` | **UI.** Ignore slowness smaller than this many milliseconds. |

## Retention
| Variable | Default | Description |
|---|---|---|
| `PULSE_RETENTION_RAW_DAYS` | `7` | **UI.** Individual check results. |
| `PULSE_RETENTION_5M_DAYS` | `30` | **UI.** 5-minute summaries (daily summaries are kept forever). |
| `PULSE_RETENTION_INCIDENT_DAYS` | `365` | **UI.** Resolved incidents and state history. |

## Agents and map
| Variable | Default | Description |
|---|---|---|
| `PULSE_AGENT_PUSH_INTERVAL_S` | `60` | How often agents report. An agent is stale after 3 × this plus 30 s. |
| `PULSE_HUB_URL` | | **Agent mode only:** the hub's public URL. |
| `PULSE_AGENT_TOKEN` | | **Agent mode only:** the token from Settings → Vantage agents. |
| `PULSE_AGENT_LOCATION` | | **Agent mode only:** a free-text location reported to the hub. |
| `PULSE_HOST_LOCATION` | | `lat,lon` of this hub, shown on the map. |
| `PULSE_MAP_STYLE_URL` | *(offline basemap)* | A MapLibre style URL, e.g. `https://tiles.openfreemap.org/styles/liberty` (keyless). |

## Integrations
| Variable | Default | Description |
|---|---|---|
| `PULSE_METRICS_ENABLED` | `true` | Serve Prometheus metrics at `/metrics`. |
| `PULSE_METRICS_TOKEN` | | Require `Authorization: Bearer <token>` for `/metrics`. |
| `PULSE_MQTT_URL` | | `mqtt://host:1883` or `mqtts://host:8883`. Turns on the MQTT bridge and Home Assistant discovery. |
| `PULSE_MQTT_USERNAME` / `PULSE_MQTT_PASSWORD` | | Broker credentials. |
| `PULSE_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant discovery prefix. |
| `PULSE_MQTT_BASE_TOPIC` | `pulse` | Base topic for states and availability. |

## Feature flags
| Variable | Default | Description |
|---|---|---|
| `PULSE_FEATURE_CORRELATION` | `true` | Upstream dependency advisories. |
| `PULSE_FEATURE_ANOMALY` | `true` | Time-of-day latency anomaly detection (otherwise a simple p95 rule). |
| `PULSE_FEATURE_CHATTER` | `false` | Community chatter spike advisories (calls Hacker News, Reddit and Bluesky search). |

## Compose-file variables
These are read by the compose files, not by Pulse itself.

| Variable | Default | Description |
|---|---|---|
| `PULSE_HOST_PORT` | `8080` | Host port published for Pulse (the left side of `host:container`). For example, `PULSE_HOST_PORT=3012` with `PULSE_PORT=3000` gives `3012:3000`. |
| `PULSE_TAG` | `edge` | Image tag in `deploy/dockhand/*.yaml`. |
| `PULSE_MEM_LIMIT` | `512m` | Container memory limit. |
| `MQTT_PORT` | `1883` | Host port for the bundled Mosquitto broker. |
