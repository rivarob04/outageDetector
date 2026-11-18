"""The monitor: schedules feed polls, probes and canaries, and turns results into verdicts."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from pulse.adapters import parse_feed
from pulse.adapters.base import FeedSnapshot
from pulse.catalog.loader import Catalog
from pulse.catalog.schema import ServiceDef
from pulse.config import Settings
from pulse.db.store import FeedChange, Store
from pulse.events import EventBus
from pulse.http import PoliteClient
from pulse.probes.baseline import Baseline, baseline, consecutive_slow, latency_ratio
from pulse.probes.canary import CanaryState, canary_specs, run_canaries
from pulse.probes.http import make_probe_client
from pulse.probes.runner import ProbeContext, run_probe
from pulse.probes.types import ProbeResult, ProbeSpec
from pulse.scheduler import Scheduler
from pulse.services import probe_specs, service_def
from pulse.verdict.engine import (
    AgentSignal,
    FeedSignal,
    PrevState,
    ProbeSignal,
    Verdict,
    VerdictConfig,
    VerdictInputs,
    evaluate,
)

log = logging.getLogger("pulse.monitor")

HISTORY_MAX = 1600  # ~26h at 60s
BASELINE_TTL = timedelta(minutes=10)
Advisor = Callable[[str], list[str]]
AnomalyFn = Callable[[str, list[tuple[datetime, float]], float], tuple[bool, float | None]]


@dataclass
class ProbeHistory:
    items: deque[tuple[datetime, bool, float | None, str | None, bool | None]] = field(
        default_factory=lambda: deque(maxlen=HISTORY_MAX)
    )
    baseline: Baseline | None = None
    baseline_at: datetime | None = None

    def add(self, r: ProbeResult) -> None:
        public_ok = r.detail.get("public_ok") if r.detail else None
        self.items.append((r.ts, r.ok, r.latency_ms, r.error, public_ok))

    def consecutive_failures(self) -> int:
        n = 0
        for item in reversed(self.items):
            if item[1]:
                break
            n += 1
        return n


@dataclass
class FeedInfo:
    indicator: str = "none"
    last_ok_at: datetime | None = None
    incident_titles: tuple[str, ...] = ()
    maintenance_titles: tuple[str, ...] = ()
    error: str | None = None


@dataclass
class AgentView:
    """Latest result per (agent, probe) pushed by remote agents."""

    ts: datetime
    ok: bool
    latency_ms: float | None
    consecutive_failures: int = 0


class Monitor:
    def __init__(self, settings: Settings, store: Store, catalog: Catalog, bus: EventBus) -> None:
        self.settings = settings
        self.store = store
        self.catalog = catalog
        self.bus = bus
        self.sched = Scheduler(settings.max_concurrency)
        self.client = PoliteClient(settings.ua, settings.http_timeout_s)
        self.probe_http: httpx.AsyncClient = make_probe_client(settings.ua)
        self.ctx = ProbeContext(
            http=self.probe_http,
            public_resolvers=settings.list_setting(settings.dns_resolvers),
            allow_private=settings.allow_private_targets,
        )
        self.canary = CanaryState()
        self.canary_specs = canary_specs(settings.list_setting(settings.canary_targets))
        self.services: dict[str, ServiceDef] = {}
        self.sources: dict[str, str] = {}
        self.specs: dict[str, list[ProbeSpec]] = {}
        self.history: dict[str, ProbeHistory] = {}
        self.feeds: dict[str, FeedInfo] = {}
        self.prev: dict[str, PrevState] = {}
        self.verdicts: dict[str, Verdict] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self.agent_views: dict[tuple[str, str], AgentView] = {}
        self.dirty: set[str] = set()
        self.advisor: Advisor | None = None
        self.anomaly: AnomalyFn | None = None
        self.vcfg = VerdictConfig(
            fail_threshold=settings.fail_threshold,
            recover_threshold=settings.recover_threshold,
        )
        self.started_at = datetime.now(UTC)
        self._lock = asyncio.Lock()
        self._persisted: dict[str, Verdict] = {}
        self._state_cache: dict[str, dict[str, Any]] | None = None

    # ---------------------------------------------------------------- lifecycle
    async def start(self, run_jobs: bool = True) -> None:
        rows = await self.store.list_services(tracked=True)
        for row in rows:
            await self._load_service(row)
        await self._load_state()
        if run_jobs:
            self._schedule_global()
            for sid in self.services:
                self._schedule_service(sid)
            self.sched.start()

    async def stop(self) -> None:
        await self.sched.stop()
        await self.client.aclose()
        await self.probe_http.aclose()

    def _schedule_global(self) -> None:
        s = self.settings
        self.sched.add("canary", self.canary_job, s.canary_interval_s, initial_delay=0)
        self.sched.add("verdict", self.verdict_job, 5, jitter=0, initial_delay=3)
        self.sched.add("verdict:all", self.verdict_all_job, 30, initial_delay=10)
        self.sched.add("rollup", self.rollup_job, 300, initial_delay=60)
        self.sched.add("retention", self.retention_job, 3600, initial_delay=120)

    def _schedule_service(self, sid: str) -> None:
        svc = self.services[sid]
        if svc.feed:
            self.sched.add(f"feed:{sid}", _bind(self.feed_job, sid), self.settings.feed_interval_s)
        for spec in self.specs.get(sid, []):
            self.sched.add(f"probe:{spec.id}", _bind(self.probe_job, spec), spec.interval_s)

    async def _load_service(self, row: dict[str, Any]) -> None:
        svc = service_def(row, self.catalog)
        if svc is None:
            return
        sid = svc.id
        self.services[sid] = svc
        self.sources[sid] = row["source"]
        specs = probe_specs(svc, self.settings, custom=row["source"] != "catalog")
        self.specs[sid] = specs
        await self.store.sync_probes(sid, specs)
        for spec in specs:
            self.history.setdefault(spec.id, ProbeHistory())

    async def _load_state(self) -> None:
        since = datetime.now(UTC) - timedelta(hours=26)
        for r in await self.store.recent_results(since):
            h = self.history.get(r["probe_id"])
            if h is not None:
                h.items.append(
                    (
                        r["ts"],
                        r["ok"],
                        r["latency_ms"],
                        r["error"],
                        (r["detail"] or {}).get("public_ok"),
                    )
                )
        for sid, row in (await self.store.feed_status()).items():
            self.feeds[sid] = FeedInfo(row["indicator"], row["last_ok_at"], error=row["error"])
        for inc in await self.store.active_incidents(self.services):
            fi = self.feeds.setdefault(inc["service_id"], FeedInfo())
            if inc["kind"] == "maintenance":
                if inc["status"] == "in_progress":
                    fi.maintenance_titles += (inc["title"],)
            else:
                fi.incident_titles += (inc["title"],)
        for sid, row in (await self.store.get_states()).items():
            self.prev[sid] = PrevState(
                row["state"],
                row["scope"],
                row["since"],
                tuple(row["reasons"] or ()),
                row["pending_state"],
                row["pending_scope"],
                row["pending_count"] or 0,
            )
            self.verdicts[sid] = Verdict(
                row["state"],
                row["scope"],
                tuple(row["reasons"] or ()),
                row["since"],
                tuple(row["advisories"] or ()),
            )
        self.dirty.update(self.services)

    # ---------------------------------------------------------------- tracking
    async def track(self, sid: str) -> None:
        async with self._lock:
            row = await self.store.get_service(sid)
            if row is None:
                raise KeyError(sid)
            if not row["tracked"]:
                await self.store.set_tracked(sid, True)
                row["tracked"] = True
            self.sched.remove_prefix(f"feed:{sid}")
            self.sched.remove_prefix(f"probe:{sid}:")
            await self._load_service(row)
            if self.sched._task is not None:
                self._schedule_service(sid)
                self.sched.trigger(f"feed:{sid}")
                for spec in self.specs.get(sid, []):
                    self.sched.trigger(f"probe:{spec.id}")
            self.dirty.add(sid)

    async def untrack(self, sid: str) -> None:
        async with self._lock:
            await self.store.set_tracked(sid, False)
            self._forget(sid)

    def _forget(self, sid: str) -> None:
        self.sched.remove_prefix(f"feed:{sid}")
        self.sched.remove_prefix(f"probe:{sid}:")
        self.services.pop(sid, None)
        for spec in self.specs.pop(sid, []):
            self.history.pop(spec.id, None)
        self.verdicts.pop(sid, None)
        self.prev.pop(sid, None)
        self.dirty.discard(sid)

    async def remove(self, sid: str) -> None:
        async with self._lock:
            self._forget(sid)
            await self.store.delete_service(sid)

    def check_now(self, sid: str) -> None:
        self.sched.trigger(f"feed:{sid}")
        for spec in self.specs.get(sid, []):
            self.sched.trigger(f"probe:{spec.id}")

    # ---------------------------------------------------------------- jobs
    async def feed_job(self, sid: str) -> None:
        svc = self.services.get(sid)
        if svc is None or svc.feed is None:
            return
        fi = self.feeds.setdefault(sid, FeedInfo())
        try:
            res = await self.client.get(svc.feed.url)
            if not res.ok:
                raise RuntimeError(f"HTTP {res.status}")
            snap = parse_feed(res.content, svc.feed)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"[:300]
            fi.error = msg
            await self.store.save_feed_error(sid, msg)
            self.dirty.add(sid)
            return
        await self.apply_snapshot(sid, snap)

    async def apply_snapshot(self, sid: str, snap: FeedSnapshot) -> FeedChange:
        fi = self.feeds.setdefault(sid, FeedInfo())
        change = await self.store.save_feed(sid, snap)
        fi.indicator = snap.indicator
        fi.last_ok_at = datetime.now(UTC)
        fi.error = None
        fi.incident_titles = tuple(i.title for i in snap.active_incidents)
        fi.maintenance_titles = tuple(i.title for i in snap.active_maintenance)
        name = self.services[sid].name if sid in self.services else sid
        for inc in change.new_incidents:
            self.bus.publish("incident", _incident_event(sid, name, "new", inc))
        for upd in change.new_updates:
            self.bus.publish(
                "incident",
                {
                    "service_id": sid,
                    "service_name": name,
                    "change": "update",
                    "incident_id": upd["incident_id"],
                    "title": upd["title"],
                    "status": upd["status"],
                    "body": upd["body"][:500],
                },
            )
        for inc in change.resolved:
            self.bus.publish("incident", _incident_event(sid, name, "resolved", inc))
        self.dirty.add(sid)
        return change

    async def probe_job(self, spec: ProbeSpec) -> None:
        if self.canary.offline:
            return  # results would only say "we're offline"; canaries cover that
        result = await run_probe(spec, self.ctx)
        await self.record_result(spec.service_id, result)

    async def record_result(self, sid: str, result: ProbeResult) -> None:
        h = self.history.setdefault(result.probe_id, ProbeHistory())
        h.add(result)
        await self.store.insert_results([result])
        self.dirty.add(sid)

    async def canary_job(self) -> None:
        if not self.canary_specs:
            return
        results = await run_canaries(self.canary_specs, self.probe_http)
        now = datetime.now(UTC)
        if self.canary.update(results, now):
            log.warning("local network %s", "OFFLINE" if self.canary.offline else "back online")
            self.bus.publish("connectivity", self.connectivity())
            self.dirty.update(self.services)

    async def verdict_job(self) -> None:
        dirty, self.dirty = self.dirty, set()
        now = datetime.now(UTC)
        for sid in dirty:
            if sid in self.services:
                await self.evaluate(sid, now)

    async def verdict_all_job(self) -> None:
        self.dirty.update(self.services)

    async def rollup_job(self) -> None:
        await self.store.rollup_5m()

    async def retention_job(self) -> None:
        s = self.settings
        counts = await self.store.retention(
            s.retention_raw_days, s.retention_5m_days, s.retention_incident_days
        )
        log.info("retention", extra={"extra_fields": counts})

    # ---------------------------------------------------------------- verdicts
    def inputs_for(self, sid: str, now: datetime) -> VerdictInputs:
        svc = self.services[sid]
        feed: FeedSignal | None = None
        if svc.feed is not None:
            fi = self.feeds.get(sid, FeedInfo())
            fresh = fi.last_ok_at is not None and (
                now - fi.last_ok_at <= timedelta(seconds=3 * self.settings.feed_interval_s + 30)
            )
            feed = FeedSignal(fresh, fi.indicator, fi.incident_titles, fi.maintenance_titles)  # type: ignore[arg-type]
        probes = tuple(
            self._probe_signal(spec, now) for spec in self.specs.get(sid, []) if spec.kind != "tls"
        )
        agents = self._agent_signals(sid, now)
        adv = tuple(self.advisor(sid)) if self.advisor else ()
        return VerdictInputs(feed, probes, agents, self.canary.offline, adv)

    def _probe_signal(self, spec: ProbeSpec, now: datetime) -> ProbeSignal:
        h = self.history.get(spec.id) or ProbeHistory()
        if not h.items:
            return ProbeSignal(spec.id, spec.kind)
        last = h.items[-1]
        degraded, ratio = False, None
        if last[1] and last[2] is not None:
            if self.anomaly is not None:
                series = [(t, lat) for t, ok, lat, _e, _p in h.items if ok and lat is not None]
                degraded, ratio = self.anomaly(spec.id, series, last[2])
            else:
                if h.baseline_at is None or now - h.baseline_at > BASELINE_TTL:
                    cutoff = now - timedelta(hours=24)
                    h.baseline = baseline(
                        [
                            lat
                            for t, ok, lat, _e, _p in h.items
                            if ok and lat is not None and t >= cutoff
                        ]
                    )
                    h.baseline_at = now
                recent_ok = [lat for _t, ok, lat, _e, _p in h.items if ok and lat is not None]
                degraded = consecutive_slow(
                    recent_ok,
                    h.baseline,
                    self.settings.latency_factor,
                    self.settings.latency_min_ms,
                    self.settings.fail_threshold,
                )
                ratio = latency_ratio(last[2], h.baseline) if degraded else None
        return ProbeSignal(
            spec.id, spec.kind, h.consecutive_failures(), last[1], ratio, degraded, last[3], last[4]
        )

    def _agent_signals(self, sid: str, now: datetime) -> tuple[AgentSignal, ...]:
        if not self.agents:
            return ()
        probe_ids = [s.id for s in self.specs.get(sid, []) if s.kind != "tls"]
        stale_after = timedelta(seconds=3 * self.settings.agent_push_interval_s + 30)
        out = []
        for aid, agent in self.agents.items():
            if agent.get("revoked"):
                continue
            seen = agent.get("last_seen_at")
            fresh = seen is not None and now - seen <= stale_after
            views = [self.agent_views[(aid, p)] for p in probe_ids if (aid, p) in self.agent_views]
            if not views:
                continue
            failing_n = sum(
                1 for v in views if v.consecutive_failures >= self.settings.fail_threshold
            )
            failing = failing_n > 0 and failing_n * 2 >= len(views)
            out.append(AgentSignal(aid, agent.get("name") or aid, fresh, failing))
        return tuple(out)

    async def evaluate(self, sid: str, now: datetime | None = None) -> Verdict:
        now = now or datetime.now(UTC)
        prev = self.prev.get(sid)
        v = evaluate(self.inputs_for(sid, now), prev, self.vcfg, now)
        self.verdicts[sid] = v
        self.prev[sid] = PrevState(
            v.state, v.scope, v.since, v.reasons, v.pending_state, v.pending_scope, v.pending_count
        )
        old = await self._persisted_state(sid)
        if old is None or _persist_needed(old, v):
            await self.store.save_state(
                sid,
                {
                    "state": v.state,
                    "scope": v.scope,
                    "reasons": list(v.reasons),
                    "advisories": list(v.advisories),
                    "since": v.since,
                    "pending_state": v.pending_state,
                    "pending_scope": v.pending_scope,
                    "pending_count": v.pending_count,
                },
                transition=v.changed,
                prev=old,
            )
            self._persisted[sid] = v
        if v.changed:
            svc = self.services[sid]
            self.bus.publish(
                "state",
                {
                    "service_id": sid,
                    "service_name": svc.name,
                    "category": svc.category,
                    "state": v.state,
                    "scope": v.scope,
                    "reasons": list(v.reasons),
                    "advisories": list(v.advisories),
                    "since": v.since.isoformat(),
                    "prev_state": old["state"] if old else None,
                    "prev_scope": old["scope"] if old else None,
                },
            )
        return v

    async def _persisted_state(self, sid: str) -> dict[str, Any] | None:
        if self._state_cache is None:
            self._state_cache = await self.store.get_states()
        cached = self._persisted.get(sid)
        if cached is not None:
            return {
                "state": cached.state,
                "scope": cached.scope,
                "reasons": list(cached.reasons),
                "advisories": list(cached.advisories),
                "pending_count": cached.pending_count,
                "pending_state": cached.pending_state,
            }
        return self._state_cache.get(sid)

    # ---------------------------------------------------------------- views
    def connectivity(self) -> dict[str, Any]:
        c = self.canary
        return {
            "offline": c.offline,
            "offline_since": c.offline_since.isoformat() if c.offline_since else None,
            "checked_at": c.last_checked.isoformat() if c.last_checked else None,
            "canaries": [
                {"target": s.url, "ok": r.ok, "latency_ms": r.latency_ms, "error": r.error}
                for s, r in zip(self.canary_specs, c.last_results, strict=False)
            ],
        }

    def sparkline(self, sid: str, points: int = 48, hours: int = 24) -> list[float | None]:
        """Average latency of the service's first HTTP probe in ``points`` buckets."""
        specs = [s for s in self.specs.get(sid, []) if s.kind == "http"] or self.specs.get(sid, [])
        if not specs:
            return []
        h = self.history.get(specs[0].id)
        if not h:
            return [None] * points
        now = datetime.now(UTC)
        start = now - timedelta(hours=hours)
        width = (hours * 3600) / points
        sums = [0.0] * points
        counts = [0] * points
        for ts, ok, lat, _e, _p in h.items:
            if ts < start or not ok or lat is None:
                continue
            i = min(points - 1, int((ts - start).total_seconds() / width))
            sums[i] += lat
            counts[i] += 1
        return [round(sums[i] / counts[i], 1) if counts[i] else None for i in range(points)]

    def last_checked(self, sid: str) -> datetime | None:
        latest = [
            h.items[-1][0]
            for s in self.specs.get(sid, [])
            if (h := self.history.get(s.id)) and h.items
        ]
        fi = self.feeds.get(sid)
        if fi and fi.last_ok_at:
            latest.append(fi.last_ok_at)
        return max(latest) if latest else None


def _bind(fn: Callable[[Any], Awaitable[None]], arg: Any) -> Callable[[], Awaitable[None]]:
    async def job() -> None:
        await fn(arg)

    return job


def _persist_needed(old: dict[str, Any], v: Verdict) -> bool:
    return (
        old.get("state") != v.state
        or old.get("scope") != v.scope
        or list(old.get("reasons") or []) != list(v.reasons)
        or list(old.get("advisories") or []) != list(v.advisories)
        or (old.get("pending_count") or 0) != v.pending_count
        or old.get("pending_state") != v.pending_state
    )


def _incident_event(sid: str, name: str, change: str, inc: dict[str, Any]) -> dict[str, Any]:
    started = inc.get("started_at")
    return {
        "service_id": sid,
        "service_name": name,
        "change": change,
        "incident_id": inc.get("id"),
        "kind": inc.get("kind"),
        "title": inc.get("title"),
        "impact": inc.get("impact"),
        "status": inc.get("status"),
        "url": inc.get("url"),
        "started_at": started.isoformat() if isinstance(started, datetime) else started,
        "regions": inc.get("regions") or [],
    }


async def gather_limited(coros: list[Awaitable[Any]], limit: int = 10) -> list[Any]:
    sem = asyncio.Semaphore(limit)

    async def one(c: Awaitable[Any]) -> Any:
        async with sem:
            return await c

    return await asyncio.gather(*(one(c) for c in coros))
