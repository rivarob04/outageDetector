"""Connectivity canaries: is *this host* online at all?"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from pulse.probes.http import run_http
from pulse.probes.types import ProbeResult, ProbeSpec


@dataclass
class CanaryState:
    """Tracks consecutive canary rounds; flips ``offline`` after ``threshold`` bad rounds."""

    threshold: int = 2
    bad_rounds: int = 0
    offline: bool = False
    offline_since: datetime | None = None
    last_results: list[ProbeResult] = field(default_factory=list)
    last_checked: datetime | None = None

    def update(self, results: list[ProbeResult], now: datetime) -> bool:
        """Record a round; returns True when ``offline`` changed."""
        self.last_results = results
        self.last_checked = now
        failed = sum(1 for r in results if not r.ok)
        majority_failed = bool(results) and failed * 2 > len(results)
        self.bad_rounds = self.bad_rounds + 1 if majority_failed else 0
        new = self.bad_rounds >= self.threshold
        changed = new != self.offline
        if changed:
            self.offline_since = now if new else None
        self.offline = new
        return changed


def canary_specs(urls: list[str], timeout_s: float = 5.0) -> list[ProbeSpec]:
    return [
        ProbeSpec(
            id=f"canary:{i}",
            service_id="_canary",
            kind="http",
            url=u,
            timeout_s=timeout_s,
            accept_status=tuple(range(400, 500)),
        )
        for i, u in enumerate(urls)
    ]


async def run_canaries(specs: list[ProbeSpec], client: httpx.AsyncClient) -> list[ProbeResult]:
    async def one(s: ProbeSpec) -> ProbeResult:
        # Any HTTP answer at all proves connectivity, so 4xx count as reachable.
        return await run_http(s, client, allow_private=True)

    if not specs:
        return []
    return list(await asyncio.gather(*(one(s) for s in specs)))


def now_utc() -> datetime:
    return datetime.now(UTC)
