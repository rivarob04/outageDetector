# Performance

Measured with `tools/perf_test.py` (run it yourself: `cd backend && uv run python ../tools/perf_test.py`). The test creates 150 services with two checks each (HTTP + TCP against a local target, so it measures Pulse rather than the internet). It then simulates 5 vantage agents, each pushing a full round of 300 results every minute. The numbers below come from a 2-vCPU Linux VM with Python 3.13 and SQLite.

| | Result |
|---|---|
| Services / checks | 150 / 300 |
| Simulated agents | 5 (each pushes 300 results/min) |
| CPU (average of one core) | **4.3%** |
| Memory (RSS) | **~143 MiB** |
| `/api/v1/summary` latency (median / max) | 36 / 51 ms |
| Idle hub, nothing watched | ~102 MiB |

**What this means for a Raspberry Pi:** with `cpus: 1` and `mem_limit: 256m` there's plenty of headroom for this load. CPU use is dominated by database writes of check results. Raising the check interval reduces both CPU and database growth proportionally.

## Hot spots fixed in Phase 9
- **In-memory check history.** It used to be a deque of tuples holding `datetime`/`float` objects (~170 bytes per sample), which reached ~77 MiB for 300 checks × 26 hours. It now uses typed arrays (~15 bytes per sample), which is **7.4 MiB** for the same history (measured).
- Sparklines, uptime and anomaly detection now read epoch floats straight from those arrays instead of building `datetime` objects for every sample.

## Where the memory goes
Most of the idle footprint is library code: FastAPI/Starlette/Pydantic (~45 MiB) and SQLAlchemy (~40 MiB). The rest (Alembic, httpx, dnspython) is under 30 MiB together. Apprise is only imported when an alert is sent, and the MQTT client only when `PULSE_MQTT_URL` is set.
