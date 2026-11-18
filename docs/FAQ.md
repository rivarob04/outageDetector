# FAQ

**Why is a service yellow (Degraded)?**
Either the provider reports a minor incident, or your checks are slower than normal for that time of day. Open the service: the "Why it's marked …" panel lists the exact reasons.

**It says "Only from your network". What does that mean?**
Your checks fail, but the provider reports no incident, and (if you run agents) other places can reach it. Common causes are your ISP, DNS or a route. If DNS is the culprit, Pulse says so.

**Everything turned grey ("You're offline")**
Most connectivity canaries failed, so Pulse can't tell anything about the services. Alerts are paused, and you get one summary when you're back. Change the canaries in Settings → Monitoring if your network blocks them.

**Why does the provider say it's fine when Pulse says it's down for everyone?**
"Widespread" means checks fail from several vantage points while the provider hasn't posted anything yet. Providers often take 10–30 minutes. The Incidents page measures this lead time.

**A site shows 403 but works in my browser**
Some sites block automated requests or cloud IP ranges. Catalog checks accept 403 where that's known. For your own services, add 403 under "Also OK".

**Can I monitor my homelab services?**
Yes: add your own service and turn on "Allow checks of local network addresses" in Settings. Agents never receive checks of private addresses.

**Does Pulse send telemetry?**
No. The only outbound requests are your checks, the status feeds you watch, and (only if you turn them on) chatter searches, a public-IP lookup, alerts and MQTT.

**How much disk does it use?**
About 320 MB after a year for 40 services at 1-minute intervals. See docs/DATABASE.md.

**How do I move from SQLite to Postgres?**
`pulse backup b.tar.gz`, then start Pulse once with `PULSE_DATABASE_URL` pointing at an empty Postgres (to migrate it), stop it, and run `pulse restore b.tar.gz` with the same `PULSE_DATABASE_URL`.

**How do I rename the app?**
Pulse is the working name. It appears in the UI wordmark, `pulse` CLI and the `PULSE_` prefix; a search/replace renames it.
