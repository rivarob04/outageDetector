"""The monitor: schedules feed polls, probes and canaries, and turns results into verdicts."""

from __future__ import annotations

import asyncio
import logging
import math
from array import array
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
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
# (probe id, [(epoch seconds, latency ms)] oldest first, current latency) -> (slow, ratio, basis)
AnomalyFn = Callable[[str, list[tuple[float, float]], float], tuple[bool, float | None, str | None]]


ERRORS: tuple[str | None, ...] = (
    None,
    "dns",
    "connect",
    "tls",
    "timeout",
    "http_status",
    "assertion",
    "ssrf_blocked",
    "other",
)
_ERR_INDEX = {e: i for i, e in enumerate(ERRORS)}
_NAN = float("nan")


class _Items:
    """Read-only, tuple-shaped view of a ProbeHistory (kept for convenience and tests)."""

    def __init__(self, h: ProbeHistory) -> None:
        self.h = h

    def __len__(self) -> int:
        return len(self.h.ts)

    def __bool__(self) -> bool:
        return len(self.h.ts) > 0

    def __getitem__(self, i: int) -> tuple[datetime, bool, float | None, str | None, bool | None]:
        e, ok, lat, err, pub = self.h.raw(i)
        return datetime.fromtimestamp(e, UTC), ok, lat, err, pub

    def __iter__(self) -> Iterator[tuple[datetime, bool, float | None, str | None, bool | None]]:
        for i in range(len(self.h.ts)):
            yield self[i]


class ProbeHistory:
    """Recent results of one probe in compact typed arrays (~15 bytes per sample).

    Python tuples of datetime/float objects cost ~170 bytes per sample, which adds up to
    hundreds of MB for a few hundred probes over 26 hours.
    """

    __slots__ = ("baseline", "baseline_at", "err", "lat", "ok", "pub", "ts")

    def __init__(self) -> None:
        self.ts = array("d")  # epoch seconds
        self.ok = array("b")
        self.lat = array("f")  # NaN = missing
        self.err = array("B")
        self.pub = array("b")  # -1 = unknown
        self.baseline: Baseline | None = None
        self.baseline_at: datetime | None = None

    @property
    def items(self) -> _Items:
        return _Items(self)

    def add(self, r: ProbeResult) -> None:
        public_ok = r.detail.get("public_ok") if r.detail else None
        self.add_raw(r.ts, r.ok, r.latency_ms, r.error, public_ok)

    def add_raw(
        self,
        ts: datetime,
        ok: bool,
        latency: float | None,
        error: str | None,
        public_ok: bool | None,
    ) -> None:
        self.ts.append(ts.timestamp())
        self.ok.append(1 if ok else 0)
        self.lat.append(_NAN if latency is None else latency)
        self.err.append(_ERR_INDEX.get(error, len(ERRORS) - 1))
        self.pub.append(-1 if public_ok is None else int(bool(public_ok)))
        if len(self.ts) > HISTORY_MAX + 200:  # trim in chunks (amortised O(1))
            for arr in (self.ts, self.ok, self.lat, self.err, self.pub):
                del arr[:200]

    def raw(self, i: int) -> tuple[float, bool, float | None, str | None, bool | None]:
        lat = self.lat[i]
        pub = self.pub[i]
        return (
            self.ts[i],
            bool(self.ok[i]),
            None if math.isnan(lat) else float(lat),
            ERRORS[self.err[i]],
            None if pub < 0 else bool(pub),
        )

    def ok_latencies(self, since_epoch: float = 0.0) -> list[tuple[float, float]]:
        """(epoch, latency) for successful checks, oldest first."""
        return [
            (t, float(lat))
            for t, ok, lat in zip(self.ts, self.ok, self.lat, strict=True)
            if ok and t >= since_epoch and not math.isnan(lat)
        ]

    def consecutive_failures(self) -> int:
        n = 0
        for ok in reversed(self.ok):
            if ok:
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
    bad_regions: frozenset[str] = frozenset()  # regions with partial/major problems


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
        self.public_ip: str | None = None
        self._canary_rounds = 0
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
        await self.load_agents()
        await self._load_agent_views()
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
        self.sched.add("agent-stale", self.agent_stale_job, 60, initial_delay=30)

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
                h.add_raw(
                    r["ts"],
                    r["ok"],
                    r["latency_ms"],
                    r["error"],
                    (r["detail"] or {}).get("public_ok"),
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
                if inc["impact"] in ("major", "critical") and inc.get("regions"):
                    fi.bad_regions = fi.bad_regions | frozenset(inc["regions"])
        for comp in await self.store.region_components():
            if comp["status"] in ("partial_outage", "major_outage"):
                fi = self.feeds.setdefault(comp["service_id"], FeedInfo())
                fi.bad_regions = fi.bad_regions | {comp["region"]}
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
        fi.bad_regions = frozenset(
            [
                c.region
                for c in snap.components
                if c.region and c.status in ("partial_outage", "major_outage")
            ]
            + [
                r
                for i in snap.active_incidents
                if i.impact in ("major", "critical")
                for r in i.regions
            ]
        )
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
        self._canary_rounds += 1
        if self.settings.public_ip_lookup_url and (self._canary_rounds % 20 == 1):
            await self._lookup_public_ip()
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

    # ---------------------------------------------------------------- agents
    async def load_agents(self) -> None:
        old = self.agents
        self.agents = {}
        for a in await self.store.list_agents():
            a["offline"] = old.get(a["id"], {}).get("offline", False)
            self.agents[a["id"]] = a
        for key in [k for k in self.agent_views if k[0] not in self.agents]:
            del self.agent_views[key]

    async def _load_agent_views(self) -> None:
        since = datetime.now(UTC) - timedelta(minutes=30)
        for r in await self.store.recent_results(since, agent=True):
            self._apply_agent_result(
                r["agent_id"], r["probe_id"], r["ts"], r["ok"], r["latency_ms"]
            )

    def _apply_agent_result(
        self, agent_id: str, probe_id: str, ts: datetime, ok: bool, latency: float | None
    ) -> None:
        prev = self.agent_views.get((agent_id, probe_id))
        if prev is not None and ts < prev.ts:
            return
        fails = 0 if ok else (prev.consecutive_failures + 1 if prev else 1)
        self.agent_views[(agent_id, probe_id)] = AgentView(ts, ok, latency, fails)

    def agent_fresh(self, agent: dict[str, Any], now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        seen = agent.get("last_seen_at")
        stale_after = timedelta(seconds=3 * self.settings.agent_push_interval_s + 30)
        return (
            not agent.get("revoked")
            and not agent.get("offline")
            and seen is not None
            and now - seen <= stale_after
        )

    async def record_agent_results(
        self,
        agent_id: str,
        results: list[ProbeResult],
        offline: bool = False,
        version: str | None = None,
        location: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        agent = self.agents.get(agent_id)
        if agent is None:
            await self.load_agents()
            agent = self.agents.get(agent_id)
            if agent is None:
                return
        was_fresh = self.agent_fresh(agent, now)
        updates: dict[str, Any] = {"last_seen_at": now}
        if version:
            updates["version"] = version
        if location and not agent.get("location"):
            updates["location"] = str(location)[:200]
        await self.store.update_agent(agent_id, **updates)
        agent.update(updates)
        agent["offline"] = offline
        if not offline and results:
            await self.store.insert_results(results, agent_id=agent_id)
            sids = {s.id: sid for sid, specs in self.specs.items() for s in specs}
            for r in sorted(results, key=lambda r: r.ts):
                self._apply_agent_result(agent_id, r.probe_id, r.ts, r.ok, r.latency_ms)
                if r.probe_id in sids:
                    self.dirty.add(sids[r.probe_id])
        if was_fresh != self.agent_fresh(agent, now):
            self.bus.publish(
                "agent",
                {
                    "agent_id": agent_id,
                    "name": agent["name"],
                    "fresh": not was_fresh,
                    "offline": offline,
                },
            )
            self.dirty.update(self.services)

    async def agent_stale_job(self) -> None:
        now = datetime.now(UTC)
        for aid, agent in self.agents.items():
            fresh = self.agent_fresh(agent, now)
            if agent.get("_fresh") is not None and agent["_fresh"] != fresh:
                self.bus.publish("agent", {"agent_id": aid, "name": agent["name"], "fresh": fresh})
                self.dirty.update(self.services)
            agent["_fresh"] = fresh

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
        h = self.history.get(spec.id)
        if h is None or not len(h.ts):
            return ProbeSignal(spec.id, spec.kind)
        _ts, last_ok, last_lat, last_err, last_pub = h.raw(-1)
        degraded, ratio, basis = False, None, None
        if last_ok and last_lat is not None:
            series = h.ok_latencies()
            if self.anomaly is not None:
                degraded, ratio, basis = self.anomaly(spec.id, series, last_lat)
            else:
                if h.baseline_at is None or now - h.baseline_at > BASELINE_TTL:
                    cutoff = (now - timedelta(hours=24)).timestamp()
                    h.baseline = baseline([lat for t, lat in series if t >= cutoff])
                    h.baseline_at = now
                degraded = consecutive_slow(
                    [lat for _t, lat in series],
                    h.baseline,
                    self.settings.latency_factor,
                    self.settings.latency_min_ms,
                    self.settings.fail_threshold,
                )
                ratio = latency_ratio(last_lat, h.baseline) if degraded else None
        return ProbeSignal(
            spec.id,
            spec.kind,
            h.consecutive_failures(),
            last_ok,
            ratio,
            degraded,
            last_err,
            last_pub,
            basis,
        )

    def _agent_signals(self, sid: str, now: datetime) -> tuple[AgentSignal, ...]:
        if not self.agents:
            return ()
        probe_ids = [s.id for s in self.specs.get(sid, []) if s.kind != "tls"]
        out = []
        for aid, agent in self.agents.items():
            if agent.get("revoked"):
                continue
            fresh = self.agent_fresh(agent, now)
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

    async def _lookup_public_ip(self) -> None:
        """Optional (off by default): ask a third-party service for our public IP."""
        try:
            res = await self.client.get(self.settings.public_ip_lookup_url, conditional=False)
            text = res.content.decode(errors="replace").strip()
            if res.ok and text.startswith("{"):
                import json

                text = str(json.loads(text).get("ip", ""))
            self.public_ip = text[:64] if res.ok and text else None
        except Exception:
            self.public_ip = None

    # ---------------------------------------------------------------- views
    def connectivity(self) -> dict[str, Any]:
        c = self.canary
        return {
            "public_ip": self.public_ip,
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
        start = datetime.now(UTC).timestamp() - hours * 3600
        width = (hours * 3600) / points
        sums = [0.0] * points
        counts = [0] * points
        for ts, lat in h.ok_latencies(start):
            i = min(points - 1, int((ts - start) / width))
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
