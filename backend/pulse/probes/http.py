"""HTTP(S) probe with a DNS / connect / TLS / TTFB timing breakdown."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from pulse.probes.ssrf import SSRFBlocked, check_host
from pulse.probes.types import ProbeResult, ProbeSpec

MAX_REDIRECTS = 5
MAX_BODY = 1_000_000


def make_probe_client(
    user_agent: str, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """A client that opens a fresh connection per request so timings are honest."""
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        follow_redirects=False,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=100),
        transport=transport,
        verify=True,
    )


async def run_http(
    spec: ProbeSpec,
    client: httpx.AsyncClient,
    allow_private: bool = False,
    resolver: Callable[[str, int, bool], Awaitable[list[str]]] = check_host,
) -> ProbeResult:
    assert spec.url
    now = datetime.now(UTC)
    url = spec.url
    timings: dict[str, float] = {}
    start = time.perf_counter()
    status: int | None = None
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                return _fail(spec, now, start, "other", {"message": f"bad url {url}"})
            port = parts.port or (443 if parts.scheme == "https" else 80)
            t0 = time.perf_counter()
            if spec.guarded or "dns_ms" not in timings:
                await resolver(parts.hostname, port, allow_private or not spec.guarded)
            timings.setdefault("dns_ms", (time.perf_counter() - t0) * 1000)
            trace = _Trace()
            resp = await client.request(
                spec.method,
                url,
                headers=dict(spec.headers),
                timeout=httpx.Timeout(spec.timeout_s, connect=min(spec.timeout_s, 5.0)),
                extensions={"trace": trace},
            )
            for k, v in trace.durations().items():
                timings.setdefault(k, v)
            if spec.guarded and not allow_private and _peer_is_private(resp):
                # DNS rebinding: the name resolved publicly, but we connected to a private address.
                await resp.aclose()
                raise SSRFBlocked(f"{parts.hostname} connected to a private address")
            status = resp.status_code
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                await resp.aclose()
                continue
            body = b""
            if spec.keyword and spec.method != "HEAD":
                body = resp.content[:MAX_BODY]
            total = (time.perf_counter() - start) * 1000
            ok = 200 <= status < 400 or status in spec.accept_status
            error = None if ok else "http_status"
            detail: dict[str, Any] = {"final_url": url} if url != spec.url else {}
            if ok and spec.keyword and spec.keyword.encode() not in body:
                ok, error = False, "assertion"
                detail["message"] = f"keyword '{spec.keyword}' not found"
            return ProbeResult(
                probe_id=spec.id,
                ts=now,
                ok=ok,
                latency_ms=round(total, 1),
                dns_ms=_r(timings.get("dns_ms")),
                connect_ms=_r(timings.get("connect_ms")),
                tls_ms=_r(timings.get("tls_ms")),
                ttfb_ms=_r(timings.get("ttfb_ms")),
                status_code=status,
                error=error,  # type: ignore[arg-type]
                detail=detail,
            )
        return _fail(spec, now, start, "other", {"message": "too many redirects"}, status)
    except SSRFBlocked as exc:
        return _fail(spec, now, start, "ssrf_blocked", {"message": str(exc)})
    except OSError as exc:  # getaddrinfo failures
        return _fail(spec, now, start, "dns", {"message": str(exc)})
    except httpx.TimeoutException:
        return _fail(spec, now, start, "timeout", {"message": "timed out"})
    except httpx.ConnectError as exc:
        kind = "tls" if "SSL" in str(exc) or "certificate" in str(exc).lower() else "connect"
        if "Name or service not known" in str(exc) or "nodename" in str(exc):
            kind = "dns"
        return _fail(spec, now, start, kind, {"message": str(exc)[:200]})
    except httpx.HTTPError as exc:
        return _fail(spec, now, start, "other", {"message": f"{type(exc).__name__}: {exc}"[:200]})


def _peer_is_private(resp: httpx.Response) -> bool:
    stream = resp.extensions.get("network_stream")
    if stream is None:
        return False
    try:
        addr = stream.get_extra_info("server_addr")
    except Exception:
        return False
    if not addr:
        return False
    from pulse.probes.ssrf import is_blocked_ip

    try:
        return is_blocked_ip(str(addr[0]))
    except ValueError:
        return False


def _r(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


def _fail(
    spec: ProbeSpec,
    now: datetime,
    start: float,
    error: str,
    detail: dict[str, Any],
    status: int | None = None,
) -> ProbeResult:
    return ProbeResult(
        probe_id=spec.id,
        ts=now,
        ok=False,
        latency_ms=round((time.perf_counter() - start) * 1000, 1),
        status_code=status,
        error=error,  # type: ignore[arg-type]
        detail=detail,
    )


class _Trace:
    """Collects httpcore trace events into phase durations."""

    _PHASES = {
        "connection.connect_tcp": "connect_ms",
        "connection.start_tls": "tls_ms",
    }

    def __init__(self) -> None:
        self.marks: dict[str, float] = {}

    async def __call__(self, name: str, info: dict[str, Any]) -> None:
        self.marks[name] = time.perf_counter()

    def durations(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for prefix, key in self._PHASES.items():
            s, e = self.marks.get(f"{prefix}.started"), self.marks.get(f"{prefix}.complete")
            if s is not None and e is not None:
                out[key] = (e - s) * 1000
        for proto in ("http11", "http2"):
            s = self.marks.get(f"{proto}.send_request_headers.started")
            e = self.marks.get(f"{proto}.receive_response_headers.complete")
            if s is not None and e is not None:
                out["ttfb_ms"] = (e - s) * 1000
        return out
