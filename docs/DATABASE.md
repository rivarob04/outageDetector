# Database sizing and retention

The figures below were measured on SQLite (WAL, after checkpoint) by inserting 100,000 probe results and rolling them up:

| Row type | Bytes per row (incl. indexes) |
|---|---|
| Raw probe result | ~164 |
| 5-minute or daily rollup | ~163 |

## Estimate: 40 services, 60 s intervals

A typical catalog service has about 2.5 probes (HTTP + DNS, sometimes 2 HTTP), so **~100 probes**.

| Data | Rows | Size |
|---|---|---|
| Raw results, 7 days (`PULSE_RETENTION_RAW_DAYS`) | 100 × 1,440/day × 7 = 1.0 M | **~165 MB** |
| 5-minute rollups, 30 days (`PULSE_RETENTION_5M_DAYS`) | 100 × 288/day × 30 = 0.86 M | **~140 MB** |
| Daily rollups, kept forever | 100 × 365/year = 36.5 k | **~6 MB per year** |
| Incidents, transitions, components | a few thousand | < 10 MB |

**After one year: about 320 MB.** The steady state is reached after 30 days, and only daily rollups (~6 MB/year) keep growing. Double the probe interval and the raw and 5-minute data halve. Agent results add the same per-probe cost for each agent.

## Retention jobs
- Hourly: delete raw results older than the raw retention, 5-minute rollups older than the 5-minute retention, resolved incidents and state transitions older than `PULSE_RETENTION_INCIDENT_DAYS` (365), notification logs older than 90 days, and agent nonces older than 10 minutes.
- Every 5 minutes: roll finished 5-minute buckets up into `rollups`, then finished days into daily rollups. Percentiles for 5-minute buckets are exact. Daily p50/p95 are approximations (the median of the bucket p50s and the p95 of the bucket p95s).

## Postgres
Set `PULSE_DATABASE_URL=postgresql://user:pass@host/db` (`postgres://` also works) and start the compose `postgres` profile. The same migrations run on start. CI runs the whole test suite against both databases.
