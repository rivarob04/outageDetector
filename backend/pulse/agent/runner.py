"""`pulse agent`: run the hub's probes from another network and push signed results.

Outbound-only: the agent never listens on a port.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import signal
from collections import deque
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from pulse import __version__
from pulse.agent.signing import parse_token, signed_headers
from pulse.config import Settings
from pulse.probes.canary import CanaryState, canary_specs, run_canaries
from pulse.probes.http import make_probe_client
from pulse.probes.runner import ProbeContext, run_probe
from pulse.probes.types import ProbeResult, ProbeSpec

log = logging.getLogger("pulse.agent")

REFRESH_PROBES_S = 300
MAX_BUFFER = 5000


class Agent:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not settings.hub_url or not settings.agent_token:
            raise SystemExit("PULSE_HUB_URL and PULSE_AGENT_TOKEN are required for `pulse agent`")
        parse_token(settings.agent_token)  # validate early
        self.settings = settings
        self.hub = settings.hub_url.rstrip("/")
        self.base_path = urlsplit(self.hub).path.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.hub,
            timeout=20,
            headers={"User-Agent": f"pulse-agent/{__version__}"},
            transport=transport,
        )
        self.probe_http = make_probe_client(settings.ua)
        # Agents always apply the SSRF guard to user-supplied (guarded) probes.
        self.ctx = ProbeContext(
            self.probe_http, settings.list_setting(settings.dns_resolvers), allow_private=False
        )
        self.canary = CanaryState()
        self.canaries = canary_specs(settings.list_setting(settings.canary_targets))
        self.probes: list[ProbeSpec] = []
        self.interval = settings.agent_push_interval_s
        self.buffer: deque[ProbeResult] = deque(maxlen=MAX_BUFFER)
        self.stopping = asyncio.Event()

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> httpx.Response:
        body = json.dumps(payload, default=str).encode() if payload is not None else b""
        full_path = f"{self.base_path}{path}"
        headers = signed_headers(self.settings.agent_token, method, full_path, body)
        if body:
            headers["content-type"] = "application/json"
        return await self.client.request(method, path, content=body or None, headers=headers)

    async def refresh_probes(self) -> None:
        r = await self._request("GET", "/api/v1/agent/probes")
        r.raise_for_status()
        data = r.json()
        self.probes = [ProbeSpec.from_dict(p) for p in data.get("probes", [])]
        self.interval = int(data.get("push_interval_s") or self.interval)
        log.info("loaded %d probes from hub as '%s'", len(self.probes), data["agent"]["name"])

    async def run_round(self) -> list[ProbeResult]:
        sem = asyncio.Semaphore(self.settings.max_concurrency)

        async def one(spec: ProbeSpec) -> ProbeResult:
            async with sem:
                return await run_probe(spec, self.ctx)

        return list(await asyncio.gather(*(one(p) for p in self.probes)))

    async def push(self, offline: bool) -> bool:
        batch = list(self.buffer)[:2000]
        payload = {
            "agent": {"version": __version__, "location": self.settings.agent_location or None},
            "offline": offline,
            "results": [r.to_dict() for r in batch],
        }
        try:
            r = await self._request("POST", "/api/v1/agent/results", payload)
        except httpx.HTTPError as exc:
            log.warning("push failed: %s (buffering %d results)", exc, len(self.buffer))
            return False
        if r.status_code in (401, 403):
            log.error("hub rejected this agent (%s): %s", r.status_code, r.text[:200])
            return False
        if r.is_success:
            for _ in batch:
                self.buffer.popleft()
            return True
        log.warning("push returned HTTP %s: %s", r.status_code, r.text[:200])
        return False

    async def loop(self) -> None:
        last_refresh = 0.0
        loop = asyncio.get_running_loop()
        while not self.stopping.is_set():
            started = loop.time()
            try:
                if not self.probes or started - last_refresh > REFRESH_PROBES_S:
                    await self.refresh_probes()
                    last_refresh = started
                canary = await run_canaries(self.canaries, self.probe_http)
                self.canary.update(canary, datetime.now(UTC))
                if self.canary.offline:
                    log.warning("this agent appears offline; skipping probes")
                else:
                    self.buffer.extend(await self.run_round())
                await self.push(offline=self.canary.offline)
            except httpx.HTTPError as exc:
                log.warning("hub unreachable: %s", exc)
            except Exception:
                log.exception("agent round failed")
            delay = max(5.0, self.interval - (loop.time() - started)) * random.uniform(0.9, 1.1)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), timeout=delay)

    async def close(self) -> None:
        await self.client.aclose()
        await self.probe_http.aclose()


async def run_agent(settings: Settings) -> None:
    agent = Agent(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, agent.stopping.set)
    log.info("pulse agent %s reporting to %s", __version__, agent.hub)
    try:
        await agent.loop()
    finally:
        await agent.close()
