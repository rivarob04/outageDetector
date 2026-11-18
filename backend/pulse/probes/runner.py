"""Dispatch a ProbeSpec to its implementation. Shared by the hub and agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from pulse.probes.dns import run_dns
from pulse.probes.http import run_http
from pulse.probes.tcp import run_tcp, run_tls
from pulse.probes.types import ProbeResult, ProbeSpec


@dataclass
class ProbeContext:
    http: httpx.AsyncClient
    public_resolvers: list[str] = field(default_factory=list)
    allow_private: bool = False


async def run_probe(spec: ProbeSpec, ctx: ProbeContext) -> ProbeResult:
    try:
        if spec.kind == "http":
            return await run_http(spec, ctx.http, ctx.allow_private)
        if spec.kind == "dns":
            return await run_dns(spec, ctx.public_resolvers)
        if spec.kind == "tcp":
            return await run_tcp(spec, ctx.allow_private)
        if spec.kind == "tls":
            return await run_tls(spec, ctx.allow_private)
    except Exception as exc:
        return ProbeResult(
            spec.id,
            datetime.now(UTC),
            False,
            error="other",
            detail={"message": f"{type(exc).__name__}: {exc}"[:200]},
        )
    raise ValueError(f"unknown probe kind {spec.kind}")
