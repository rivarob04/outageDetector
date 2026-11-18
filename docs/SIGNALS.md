# Advanced signals (Phase 8)

Each signal can be switched off. None of them makes network calls in tests.

## Upstream correlation (`PULSE_FEATURE_CORRELATION`, on by default)
Catalog entries can declare `depends_on`, with the evidence in `depends_on_sources`:

```yaml
depends_on: ["aws:us-east-1"]      # a region of a provider
depends_on: ["aws"]                # or a whole provider
```

When the upstream is **watched** and has problems, the dependent service gets an advisory such as "Possibly affected: AWS N. Virginia (us-east-1) is having problems." on its tile, detail page, alerts and MQTT attributes.
- A **region** dependency triggers when that region has a partial or major component problem, or a major/critical incident in it.
- A **whole-provider** dependency triggers when the provider's verdict is a partial or major outage (unless that's only local).
- Advisories **never change the state**. A green service stays green, with context.
- Custom services can declare `depends_on` too.

## Latency anomaly detection (`PULSE_FEATURE_ANOMALY`, on by default)
This replaces the simple "3 × p95" rule with a time-of-day aware detector. See docs/verdict.md for the exact maths. In short: an EWMA of recent latency is compared with the median and MAD of the same **hour of day** over 7 days of 5-minute rollups. With too little hourly history it falls back to the last 24 hours. A single slow request never triggers it, and neither do tiny absolute increases (`PULSE_LATENCY_MIN_MS`). Hourly baselines refresh every 30 minutes.

## Community chatter (`PULSE_FEATURE_CHATTER`, **off** by default)
Every 10 minutes, Pulse counts public posts from the last 30 minutes that contain phrases like `"github down"` (from the catalog's `chatter_keywords`, or "<name> down" / "<name> outage"). The sources are:

| Source | API | Notes |
|---|---|---|
| Hacker News | `hn.algolia.com/api/v1/search_by_date` | Public search API |
| Reddit | `reddit.com/search.json` | Often blocks cloud/datacentre IPs; works from most home connections |
| Bluesky | `public.api.bsky.app` `searchPosts` | Same caveat as Reddit |

A **spike** is at least 3 posts *and* at least 4 × (the 24-hour average + 0.5). It shows as a weak advisory ("Chatter spike: 18 public posts in the last 30 minutes mention it being down") and **never** sets a status by itself. Each source fails independently, and Pulse doesn't scrape sites whose terms forbid it (e.g. Downdetector).

## "Before they admit it" lead time
For every time a service became **widespread** (failing from several vantage points), Pulse looks for a provider incident that it first saw between 30 minutes before and 6 hours after that moment.
- **Lead time** is the time from Pulse's widespread verdict to the moment Pulse first saw the provider's post. It's positive when Pulse was first. It includes up to one feed-poll interval of delay.
- Episodes with no matching incident after 6 hours count as **unconfirmed**.

It's shown per provider in **Incidents → Time to recover** and in `/api/v1/incidents/stats`. It needs vantage agents, because without them nothing can be widespread.
