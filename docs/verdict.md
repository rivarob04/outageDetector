# Verdict rules

The verdict engine (`backend/pulse/verdict/engine.py`) turns every signal for a service into one **state**, one **scope** and a list of **reasons**. The reasons are shown verbatim in the UI's "Why is this red?" panel. The engine is a pure function, and every rule below has a test in `backend/tests/test_verdict.py`.

## Signals

| Signal | Meaning |
|---|---|
| Feed | The provider's official status feed. It is *fresh* when fetched successfully within 3 × the poll interval. Its indicator (`none`, `minor`, `major`, `critical`, `maintenance`) maps to a state: `operational`, `degraded`, `partial_outage`, `major_outage`, `maintenance`. |
| Local probes | HTTP/DNS/TCP/TLS checks from this host. A probe is **failing** after `fail_threshold` (default 2) consecutive failures. It is **slow** when its latest latencies are above the latency baseline (see below). |
| Vantage agents | Remote `pulse agent`s. Only fresh agents count (seen within 3 × their push interval). For each service an agent is failing, slow or OK. |
| Canaries | Connectivity checks for this host. `local_network` turns true after 2 rounds in which most canaries fail. |

A **vantage point** is this host (when it has probes for the service) or a fresh agent.

## Decision table (first matching row wins)

| # | Condition | State | Scope |
|---|---|---|---|
| 1 | `local_network` and the feed is fresh and not operational | feed state | `provider_confirmed` |
| 2 | `local_network` otherwise | `unknown` | `local_network` |
| 3 | Fresh feed reports an incident or maintenance | feed state. It escalates to the probe-derived state when that is worse **and** widespread. | `provider_confirmed` |
| 4 | ≥ 2 vantage points are failing **and** ≥ `widespread_ratio` (50%) of them are failing | `major_outage` if all points fail, else `partial_outage` | `widespread` |
| 5 | Local probes failing (all of them → `major_outage`, some → `partial_outage`) | as described | `local_only` |
| 6 | Any vantage point slow (latency) | `degraded` | `widespread` if ≥ 2 points are slow, else `local_only` |
| 7 | Any data, and nothing above matched | `operational` | `none` |
| 8 | No data at all | `unknown` | `none` |

Notes:
- **DNS-only failures.** If a DNS probe fails through the system resolver but succeeds through the public resolvers (1.1.1.1, 8.8.8.8, 9.9.9.9 by default), the failure can't count as widespread. The reasons blame your DNS.
- A **single failing agent** with a healthy local view isn't enough for rule 4. It appears as a note in the reasons.
- If the provider says operational but checks fail widely (rule 4), you see "The provider hasn't posted an incident yet." Phase 8 measures how long providers take to confirm ("before they admit it").

## Flap protection

- Rows 1–3 (provider and local network) and the first verdict for a service apply **immediately**.
- A change that only refines the scope (same state, different scope) applies immediately.
- Any other change must persist for `change_threshold` evaluations (default 2), or `recover_threshold` (default 3) when moving back to `operational`. Until then the previous state is kept and a "Confirming change to …" reason is shown.
- Leaving `unknown` is immediate.

## Latency baseline (Phase 3 rule)

- The baseline is the p50/p95 of successful latencies for the probe over the last 24 h, from at least 20 samples.
- A probe is **slow** when each of its last `fail_threshold` latencies is above `latency_factor` × p95 (default 3) **and** above `latency_min_ms` (default 300 ms).
- The reason shows the ratio to p50 ("latency is 4.2× its normal level").
- **Phase 8 detector (default, `PULSE_FEATURE_ANOMALY=true`).** The baseline per probe and **hour of day** is the median and MAD of 5-minute medians over the last 7 days. With fewer than 12 samples for that hour, the detector falls back to the last 24 h with no hour split. The current latency is an EWMA (α = 0.4) of recent successful checks. A probe is slow when **all** of these hold:
  - the robust z-score `(ewma − median) / (1.4826 × MAD)` ≥ 4;
  - `ewma ≥ latency_factor × median`;
  - `ewma − median ≥ latency_min_ms`.
  The reason then says, e.g., "HTTP check latency is 4.2× its normal level for this hour".

## Advisories

Upstream-dependency advisories (Phase 8, e.g. "possibly affected via AWS us-east-1") are passed through unchanged. They **never** change the state.
