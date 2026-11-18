"""Load test: N custom services (2 checks each) + simulated agents against a running hub.

Usage (from backend/):  uv run python ../tools/perf_test.py --services 150 --agents 5 --minutes 5

Starts a tiny local HTTP target and a hub with PULSE_ALLOW_PRIVATE_TARGETS=true, creates the
services through the API, simulates agents pushing signed results every minute, and samples the
hub's CPU and memory. Prints a Markdown table for docs/PERFORMANCE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx

from pulse.agent.signing import signed_headers
from pulse.probes.types import ProbeResult

PORT = 8765
TARGET = 9765


class Target(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a: object) -> None:
        pass


def sample(pid: int) -> tuple[float, float]:
    """(cpu seconds used so far, RSS MiB)"""
    with open(f"/proc/{pid}/stat") as f:
        parts = f.read().split(")")[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    cpu = (int(parts[11]) + int(parts[12])) / ticks
    with open(f"/proc/{pid}/status") as f:
        rss = next(int(line.split()[1]) for line in f if line.startswith("VmRSS")) / 1024
    return cpu, rss


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--services", type=int, default=150)
    ap.add_argument("--agents", type=int, default=5)
    ap.add_argument("--minutes", type=float, default=5)
    args = ap.parse_args()

    srv = ThreadingHTTPServer(("127.0.0.1", TARGET), Target)
    Thread(target=srv.serve_forever, daemon=True).start()
    data = tempfile.mkdtemp(prefix="pulse-perf-")
    env = {**os.environ, "PULSE_DATA_DIR": data, "PULSE_LOG_JSON": "false", "PULSE_LOG_LEVEL": "WARNING",
           "PULSE_ADMIN_USERNAME": "admin", "PULSE_ADMIN_PASSWORD": "perf-password-123",
           "PULSE_ALLOW_PRIVATE_TARGETS": "true", "PULSE_CANARY_TARGETS": f"http://127.0.0.1:{TARGET}/c"}
    hub = subprocess.Popen([sys.executable, "-m", "pulse", "serve", "--port", str(PORT)], env=env)
    base = f"http://127.0.0.1:{PORT}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30) as c:
            for _ in range(60):
                try:
                    if (await c.get("/readyz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            csrf = (await c.post("/api/v1/auth/login", json={"username": "admin",
                                                              "password": "perf-password-123"})).json()["csrf"]
            h = {"x-csrf-token": csrf}
            t0 = time.monotonic()
            for i in range(args.services):
                r = await c.post("/api/v1/services", headers=h, json={
                    "name": f"Perf {i}", "probes": [
                        {"kind": "http", "url": f"http://127.0.0.1:{TARGET}/s{i}"},
                        {"kind": "tcp", "host": "127.0.0.1", "port": TARGET}]})
                r.raise_for_status()
            setup_s = time.monotonic() - t0
            tokens = []
            for i in range(args.agents):
                r = await c.post("/api/v1/agents", headers=h, json={"name": f"agent-{i}", "lat": 0, "lon": i})
                tokens.append(r.json()["token"])
            probe_ids = [f"perf-{i}:{k}:0" for i in range(args.services) for k in ("http", "tcp")]

            async def agent_loop(token: str) -> None:
                while True:
                    now = datetime.now(UTC)
                    payload = {"results": [ProbeResult(p, now, True, 20.0).to_dict() for p in probe_ids]}
                    body = json.dumps(payload).encode()
                    headers = signed_headers(token, "POST", "/api/v1/agent/results", body)
                    await c.post("/api/v1/agent/results", content=body,
                                 headers={**headers, "content-type": "application/json"})
                    await asyncio.sleep(60)

            agents = [asyncio.create_task(agent_loop(t)) for t in tokens]
            await asyncio.sleep(30)  # warm up
            cpu0, _ = sample(hub.pid)
            start = time.monotonic()
            rss_samples = []
            api_ms = []
            while time.monotonic() - start < args.minutes * 60:
                await asyncio.sleep(10)
                rss_samples.append(sample(hub.pid)[1])
                t = time.perf_counter()
                await c.get("/api/v1/summary")
                api_ms.append((time.perf_counter() - t) * 1000)
            cpu1, _ = sample(hub.pid)
            elapsed = time.monotonic() - start
            for a in agents:
                a.cancel()
            counts = (await c.get("/api/v1/settings", headers=h)).json()["stats"]
        db = sum(os.path.getsize(os.path.join(data, f)) for f in os.listdir(data) if f.startswith("pulse.db"))
        print(f"| Services / checks | {args.services} / {len(probe_ids)} |")
        print(f"| Simulated agents | {args.agents} (each pushes {len(probe_ids)} results/min) |")
        print(f"| Measured for | {elapsed / 60:.1f} min after 30 s warm-up |")
        print(f"| CPU (average of one core) | {100 * (cpu1 - cpu0) / elapsed:.1f}% |")
        print(f"| Memory (RSS) median / max | {statistics.median(rss_samples):.0f} / {max(rss_samples):.0f} MiB |")
        print(f"| /api/v1/summary latency median / max | {statistics.median(api_ms):.0f} / {max(api_ms):.0f} ms |")
        print(f"| Service setup through the API | {setup_s:.1f} s |")
        print(f"| Check results stored | {counts['probe_results']:,} |")
        print(f"| Database size | {db / 1e6:.1f} MB |")
    finally:
        hub.send_signal(signal.SIGTERM)
        hub.wait(10)
        srv.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
