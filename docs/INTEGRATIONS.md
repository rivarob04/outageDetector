# Alerts and integrations

## Alerts
Add channels in **Settings → Alerts**. Each channel gets a rule you can adjust:

| Setting | Meaning |
|---|---|
| Alert me about | The minimum state: degraded, partial outage or major outage. |
| For | All watched services, some categories, or some services. |
| When the problem is | The scopes that alert: confirmed by the provider, failing from several places, failing only from my network. The default leaves out "only from my network". |
| Quiet hours | No alerts between these times, in the rule's time zone. Windows across midnight such as 22:00–07:00 work. Suppressed alerts are logged. |
| Don't repeat within | Cooldown per service, which stops a flapping service from paging you repeatedly. |
| Tell me when it recovers | Sends a recovery message, but only for problems you were alerted about. |
| Daily digest | Sends a summary at 08:00 in the rule's time zone. |

**Offline pause:** while this host's connectivity canaries fail, no alerts go out. When you're back online you get one "You were offline for N min" message.

### Apprise URLs
Pulse uses [Apprise](https://github.com/caronc/apprise/wiki), so most services work by URL. Some examples:

| Service | URL |
|---|---|
| ntfy | `ntfys://ntfy.sh/your-topic` or `ntfy://user:pass@your-ntfy-host/topic` |
| Discord | `discord://webhook_id/webhook_token` |
| Slack | `slack://TokenA/TokenB/TokenC/#channel` |
| Telegram | `tgram://bot_token/chat_id` |
| Pushover | `pover://user_key@app_token` |
| Gotify | `gotifys://host/token` |
| Email | `mailtos://user:password@gmail.com` |
| Home Assistant | `hassio://host/long_lived_token` |

Use **Send test** to check a channel. Channel URLs are stored in the database and masked in the UI and API. Exports never include them.

### Webhooks
A webhook channel POSTs JSON such as:

```json
{"event": "problem", "service_id": "github", "service_name": "GitHub", "state": "major_outage",
 "scope": "widespread", "reasons": ["…"], "prev_state": "operational", "title": "…", "text": "…"}
```

`event` is one of `problem`, `recovery`, `offline_summary`, `digest` or `test`. Each request carries `X-Pulse-Timestamp` and `X-Pulse-Signature: sha256=<hex HMAC-SHA256(secret, timestamp + "." + body)>`. The secret is shown once when the channel is created.

## Home Assistant (MQTT discovery)
1. Run an MQTT broker (the compose `mqtt` profile starts Mosquitto) and set `PULSE_MQTT_URL=mqtt://mosquitto:1883`. Also set `PULSE_MQTT_USERNAME`/`PULSE_MQTT_PASSWORD` if needed, and use `mqtts://` for TLS.
2. In Home Assistant, add the **MQTT** integration pointing at the same broker.
3. Every watched service appears as a device "<Service> (Pulse)" with these entities:
   - **Status** (enum: operational, maintenance, degraded, partial_outage, major_outage, unknown) with reasons as attributes
   - **Scope** (provider_confirmed, widespread, local_only, local_network, none)
   - **Problem** (binary sensor, device class `problem`): ideal for automations
   - **Latency** (ms) and **Active incidents**

Topics: `pulse/<service>/state` (JSON, retained) and `pulse/status` (`online`/`offline`, used as the last will). Change the topics with `PULSE_MQTT_BASE_TOPIC` and `PULSE_MQTT_DISCOVERY_PREFIX`.

Example automation:

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.github_pulse_problem
    to: "on"
action:
  - service: notify.mobile_app_phone
    data:
      message: "GitHub: {{ state_attr('sensor.github_pulse_status', 'reasons')[0] }}"
```

## Prometheus and Grafana
`/metrics` exposes `pulse_service_state`, `pulse_service_up`, `pulse_service_scope`, `pulse_service_uptime_ratio_90d`, `pulse_probe_up`, `pulse_probe_latency_ms`, `pulse_probe_consecutive_failures`, `pulse_active_incidents`, `pulse_agent_fresh` and `pulse_local_network_offline`. Set `PULSE_METRICS_TOKEN` to require `Authorization: Bearer <token>` (or `?token=`). Set `PULSE_METRICS_ENABLED=false` to turn the endpoint off. See `deploy/prometheus.yml`, and import `deploy/grafana-dashboard.json` into Grafana.

## Feeds and badges
- `/feed/incidents.atom`: provider incidents and maintenance (last 30 days).
- `/feed/maintenance.ics`: scheduled maintenance as a subscribable calendar.
- `/badge/<service>.svg`: a status badge, e.g. `![GitHub](https://pulse.example.com/badge/github.svg)`.

These follow `PULSE_PUBLIC_VIEW`: when viewing is private, they need a signed-in session.

## Public status page
In **Settings → Public status page**, turn it on, pick services and give it a title. It's served at `/status` (HTML, auto-refreshing) and `/status.json` **even when the dashboard is private**, and it shows only the selected services. Problems that are only on your network show as operational there, because a status page for others shouldn't blame the service for your connection.
