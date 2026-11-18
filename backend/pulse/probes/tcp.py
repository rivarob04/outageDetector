"""TCP connect and TLS certificate probes."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import time
from datetime import UTC, datetime

from pulse.probes.ssrf import SSRFBlocked, check_host
from pulse.probes.types import ProbeResult, ProbeSpec

TLS_WARN_DAYS = 14


async def run_tcp(spec: ProbeSpec, allow_private: bool = False) -> ProbeResult:
    assert spec.host and spec.port
    now = datetime.now(UTC)
    start = time.perf_counter()
    try:
        await check_host(spec.host, spec.port, allow_private or not spec.guarded)
        dns_ms = (time.perf_counter() - start) * 1000
        t0 = time.perf_counter()
        _r, w = await asyncio.wait_for(
            asyncio.open_connection(spec.host, spec.port), timeout=spec.timeout_s
        )
        connect_ms = (time.perf_counter() - t0) * 1000
        w.close()
        await w.wait_closed()
        return ProbeResult(
            spec.id,
            now,
            True,
            round((time.perf_counter() - start) * 1000, 1),
            dns_ms=round(dns_ms, 1),
            connect_ms=round(connect_ms, 1),
        )
    except SSRFBlocked as exc:
        return ProbeResult(spec.id, now, False, error="ssrf_blocked", detail={"message": str(exc)})
    except TimeoutError:
        return ProbeResult(spec.id, now, False, round(spec.timeout_s * 1000, 1), error="timeout")
    except OSError as exc:
        kind = "dns" if isinstance(exc, __import__("socket").gaierror) else "connect"
        return ProbeResult(spec.id, now, False, error=kind, detail={"message": str(exc)[:200]})  # type: ignore[arg-type]


async def run_tls(spec: ProbeSpec, allow_private: bool = False) -> ProbeResult:
    assert spec.host
    port = spec.port or 443
    now = datetime.now(UTC)
    start = time.perf_counter()
    ctx = ssl.create_default_context()
    try:
        await check_host(spec.host, port, allow_private or not spec.guarded)
        _r, w = await asyncio.wait_for(
            asyncio.open_connection(spec.host, port, ssl=ctx, server_hostname=spec.host),
            timeout=spec.timeout_s,
        )
        sslobj = w.get_extra_info("ssl_object")
        cert = sslobj.getpeercert() if sslobj else None
        w.close()
        with contextlib.suppress(ssl.SSLError, OSError):
            await w.wait_closed()
        latency = round((time.perf_counter() - start) * 1000, 1)
        if not cert:
            return ProbeResult(
                spec.id, now, False, latency, error="tls", detail={"message": "no certificate"}
            )
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), UTC)
        days = (expires - now).total_seconds() / 86400
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "")
        return ProbeResult(
            spec.id,
            now,
            days > 0,
            latency,
            tls_ms=latency,
            error=None if days > 0 else "tls",
            detail={
                "expires_at": expires.isoformat(),
                "days_left": round(days, 1),
                "issuer": issuer,
                "warn": days < TLS_WARN_DAYS,
            },
        )
    except SSRFBlocked as exc:
        return ProbeResult(spec.id, now, False, error="ssrf_blocked", detail={"message": str(exc)})
    except TimeoutError:
        return ProbeResult(spec.id, now, False, round(spec.timeout_s * 1000, 1), error="timeout")
    except ssl.SSLError as exc:
        return ProbeResult(spec.id, now, False, error="tls", detail={"message": str(exc)[:200]})
    except OSError as exc:
        return ProbeResult(spec.id, now, False, error="connect", detail={"message": str(exc)[:200]})
