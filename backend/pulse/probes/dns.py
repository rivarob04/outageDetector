"""DNS probe: system resolver vs. public resolvers."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.resolver

from pulse.probes.types import ProbeResult, ProbeSpec


async def _lookup(host: str, resolver: dns.asyncresolver.Resolver) -> tuple[bool, float, str]:
    start = time.perf_counter()
    try:
        answer = await resolver.resolve(host, "A", search=False)
        ok = len(answer) > 0
        msg = ",".join(sorted(r.to_text() for r in answer))[:120]
    except dns.resolver.NoAnswer:
        try:
            answer = await resolver.resolve(host, "AAAA", search=False)
            ok, msg = len(answer) > 0, "AAAA only"
        except dns.exception.DNSException as exc:
            ok, msg = False, type(exc).__name__
    except dns.exception.DNSException as exc:
        ok, msg = False, type(exc).__name__
    return ok, (time.perf_counter() - start) * 1000, msg


def make_resolver(nameserver: str | None, timeout_s: float) -> dns.asyncresolver.Resolver:
    if nameserver:
        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers = [nameserver]
    else:
        r = dns.asyncresolver.Resolver()
    r.lifetime = timeout_s
    r.timeout = timeout_s
    return r


async def run_dns(spec: ProbeSpec, public_resolvers: list[str]) -> ProbeResult:
    assert spec.host
    now = datetime.now(UTC)
    timeout = min(spec.timeout_s, 5.0)
    ok, ms, msg = await _lookup(spec.host, make_resolver(None, timeout))
    detail: dict[str, Any] = {"system": {"ok": ok, "ms": round(ms, 1), "answer": msg}}
    public_ok: bool | None = None
    # Only consult public resolvers when the system resolver fails (keeps polling cheap).
    if not ok and public_resolvers:
        results = []
        for ns in public_resolvers:
            p_ok, p_ms, p_msg = await _lookup(spec.host, make_resolver(ns, timeout))
            results.append(p_ok)
            detail[ns] = {"ok": p_ok, "ms": round(p_ms, 1), "answer": p_msg}
        public_ok = any(results)
    detail["public_ok"] = public_ok
    return ProbeResult(
        probe_id=spec.id,
        ts=now,
        ok=ok,
        latency_ms=round(ms, 1),
        dns_ms=round(ms, 1),
        error=None if ok else "dns",
        detail=detail,
    )
