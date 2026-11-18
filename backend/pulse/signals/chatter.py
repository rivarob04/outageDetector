"""Community chatter (off by default): spikes in public posts saying "<service> down".

A weak signal only: it adds an advisory and never changes a service's state.
Sources are ToS-friendly public search APIs; each fails independently.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import select

from pulse.adapters.base import load_json
from pulse.db.store import floor_time, upsert
from pulse.db.tables import chatter_t

log = logging.getLogger("pulse.chatter")

BUCKET_S = 600
WINDOW = timedelta(minutes=30)
MIN_POSTS = 3
SPIKE_FACTOR = 4.0

Source = Callable[[Any, str, datetime], Awaitable[int]]


def _quoted(phrase: str) -> str:
    """URL-encode an exact-phrase search ("github down")."""
    return quote(f'"{phrase}"')


async def hn(client: Any, phrase: str, since: datetime) -> int:
    url = (
        "https://hn.algolia.com/api/v1/search_by_date?tags=(story,comment)&hitsPerPage=0"
        f"&query={_quoted(phrase)}&numericFilters=created_at_i>{int(since.timestamp())}"
    )
    res = await client.get(url, conditional=False)
    if not res.ok:
        raise RuntimeError(f"HTTP {res.status}")
    return int(load_json(res.content).get("nbHits", 0))


async def reddit(client: Any, phrase: str, since: datetime) -> int:
    url = f"https://www.reddit.com/search.json?q={_quoted(phrase)}&sort=new&limit=100&t=hour"
    res = await client.get(url, conditional=False)
    if not res.ok:
        raise RuntimeError(f"HTTP {res.status}")
    posts = load_json(res.content).get("data", {}).get("children", [])
    return sum(1 for p in posts if p.get("data", {}).get("created_utc", 0) >= since.timestamp())


async def bluesky(client: Any, phrase: str, since: datetime) -> int:
    url = (
        "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?limit=100&sort=latest"
        f"&q={_quoted(phrase)}&since={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    res = await client.get(url, conditional=False)
    if not res.ok:
        raise RuntimeError(f"HTTP {res.status}")
    return len(load_json(res.content).get("posts", []))


SOURCES: dict[str, Source] = {"hn": hn, "reddit": reddit, "bluesky": bluesky}


def is_spike(current: int, history: list[int]) -> tuple[bool, float]:
    base = statistics.fmean(history) if history else 0.0
    return current >= MIN_POSTS and current >= SPIKE_FACTOR * (base + 0.5), base


class Chatter:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.spikes: dict[str, str] = {}
        self.failures: dict[str, str] = {}

    def phrases(self, service_id: str) -> list[str]:
        svc = self.rt.monitor.services[service_id]
        if svc.chatter_keywords:
            return svc.chatter_keywords[:3]
        short = svc.name.split(" (")[0]
        return [f"{short} down", f"{short} outage"]

    async def job(self) -> None:
        mon, store = self.rt.monitor, self.rt.store
        now = datetime.now(UTC)
        bucket = floor_time(now, BUCKET_S)
        for sid in [s for s, src in mon.sources.items() if src == "catalog"]:
            total, sources_ok = 0, []
            for name, fn in SOURCES.items():
                try:
                    total += sum([await fn(mon.client, p, now - WINDOW) for p in self.phrases(sid)])
                    sources_ok.append(name)
                    self.failures.pop(name, None)
                except Exception as exc:
                    self.failures[name] = str(exc)[:120]
            if not sources_ok:
                continue
            async with store.engine.begin() as c:
                await upsert(
                    c,
                    chatter_t,
                    {"service_id": sid, "source": "all", "bucket": bucket, "count": total},
                    ["service_id", "source", "bucket"],
                )
                rows = await c.execute(
                    select(chatter_t.c.count).where(
                        chatter_t.c.service_id == sid,
                        chatter_t.c.source == "all",
                        chatter_t.c.bucket < bucket,
                        chatter_t.c.bucket >= bucket - timedelta(days=1),
                    )
                )
                history = [r[0] for r in rows]
            spike, base = is_spike(total, history)
            if spike:
                self.spikes[sid] = (
                    f"Chatter spike: {total} public posts in the last 30 minutes "
                    f"mention it being down (usually about {base:.0f})."
                )
            else:
                self.spikes.pop(sid, None)
            mon.dirty.add(sid)

    def advisories(self, service_id: str) -> list[str]:
        msg = self.spikes.get(service_id)
        return [msg] if msg else []
