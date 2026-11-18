"""Polite shared HTTP client.

Every outbound request to third parties goes through :class:`PoliteClient`:
descriptive User-Agent, timeouts, per-host spacing, retries with backoff and
conditional requests (ETag / Last-Modified) so unchanged feeds cost ~nothing.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

RETRY_STATUS = {429, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str
    elapsed_ms: float
    not_modified: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 or self.not_modified


@dataclass
class _CacheEntry:
    etag: str | None
    last_modified: str | None
    content: bytes
    content_type: str


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        timeout_s: float = 10.0,
        min_host_gap_s: float = 1.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/json, */*;q=0.8"},
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0)),
            follow_redirects=True,
            transport=transport,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._gap = min_host_gap_s
        self._retries = retries
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._cache: dict[str, _CacheEntry] = {}

    @property
    def raw(self) -> httpx.AsyncClient:
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _space(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0.0) + self._gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def get(self, url: str, *, conditional: bool = True) -> FetchResult:
        host = urlsplit(url).hostname or ""
        headers: dict[str, str] = {}
        cached = self._cache.get(url) if conditional else None
        if cached:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified

        attempt = 0
        while True:
            await self._space(host)
            start = time.perf_counter()
            try:
                resp = await self._client.get(url, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt >= self._retries:
                    raise
                attempt += 1
                await asyncio.sleep(_backoff(attempt))
                continue
            elapsed = (time.perf_counter() - start) * 1000
            if resp.status_code in RETRY_STATUS and attempt < self._retries:
                attempt += 1
                retry_after = _retry_after(resp)
                await asyncio.sleep(retry_after if retry_after is not None else _backoff(attempt))
                continue
            break

        ctype = resp.headers.get("content-type", "")
        if resp.status_code == 304 and cached:
            return FetchResult(
                url, 200, cached.content, cached.content_type, elapsed, True, dict(resp.headers)
            )
        if conditional and resp.is_success:
            etag, lm = resp.headers.get("etag"), resp.headers.get("last-modified")
            if etag or lm:
                self._cache[url] = _CacheEntry(etag, lm, resp.content, ctype)
        return FetchResult(
            url, resp.status_code, resp.content, ctype, elapsed, False, dict(resp.headers)
        )


def _backoff(attempt: int) -> float:
    return min(8.0, 0.5 * 2**attempt) * (0.75 + random.random() / 2)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if value and value.isdigit():
        return min(30.0, float(value))
    return None
