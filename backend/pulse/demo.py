"""Demo mode (``PULSE_DEMO=1``): realistic fake data covering every state and scope.

No network calls are made. The real verdict engine evaluates the fake signals, so
what the UI shows is exactly what Pulse would conclude from such data.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pulse.adapters.base import Component, FeedSnapshot, Incident, IncidentUpdate
from pulse.db.store import floor_time, upsert
from pulse.db.tables import rollups_t
from pulse.monitor import AgentView, ProbeHistory
from pulse.probes.types import ProbeResult

log = logging.getLogger("pulse.demo")


@dataclass
class Scenario:
    sid: str
    base_ms: float
    mode: str = "ok"  # ok | fail | slow | flaky
    agents_fail: int = 0  # how many demo agents see failures
    feed: str = "none"  # none | minor | major | critical | maintenance
    title: str = ""


SCENARIOS = [
    Scenario("github", 95, "fail", 3, "major", "Incident with Git operations and Actions"),
    Scenario("aws", 140, "ok", 0, "critical", "Increased error rates in US-EAST-1"),
    Scenario("cloudflare", 25, "ok", 0, "minor", "Elevated latency in some data centers"),
    Scenario("slack", 120, "ok", 0, "maintenance", "Scheduled database maintenance"),
    Scenario("openai", 210, "fail", 2),
    Scenario("steam", 160, "fail", 0),
    Scenario("discord", 70, "slow"),
    Scenario("anthropic", 110),
    Scenario("netflix", 60),
    Scenario("spotify", 80),
    Scenario("youtube", 45),
    Scenario("npm", 55),
    Scenario("pypi", 50),
    Scenario("docker-hub", 130, "flaky"),
    Scenario("tailscale", 90),
    Scenario("zoom", 100),
    Scenario("google", 35),
    Scenario("vercel", 85),
]

CUSTOM = [
    ("home-nas", "Home NAS", "http://192.168.1.10:5000", 4),
    ("jellyfin", "Jellyfin", "http://192.168.1.20:8096/health", 7),
]

AGENTS = [
    ("demo-yyz", "Toronto VPS", "Toronto, CA", 43.65, -79.38),
    ("demo-fra", "Frankfurt Pi", "Frankfurt, DE", 50.11, 8.68),
    ("demo-sin", "Singapore VPS", "Singapore", 1.35, 103.82),
]

CF_DOWN = ["YYZ", "ORD", "EWR"]


def _latency(base: float, t: datetime, rng: random.Random) -> float:
    diurnal = 1 + 0.15 * math.sin((t.hour + t.minute / 60) / 24 * 2 * math.pi)
    return round(max(2.0, base * diurnal * rng.lognormvariate(0, 0.12)), 1)


def _result(
    pid: str,
    sc: Scenario,
    t: datetime,
    now: datetime,
    rng: random.Random,
    ok_override: bool | None = None,
) -> ProbeResult:
    recent = now - t < timedelta(minutes=25)
    fail = (
        ok_override is False
        or (sc.mode == "fail" and recent)
        or (sc.mode == "flaky" and rng.random() < 0.03)
    )
    if fail:
        return ProbeResult(
            pid,
            t,
            False,
            10000.0 if rng.random() < 0.5 else None,
            error="timeout" if rng.random() < 0.6 else "connect",
            detail={"message": "demo failure"},
        )
    lat = _latency(sc.base_ms, t, rng)
    if sc.mode == "slow" and recent:
        lat = round(lat * 6 + 300, 1)
    return ProbeResult(
        pid,
        t,
        True,
        lat,
        round(lat * 0.05, 1),
        round(lat * 0.12, 1),
        round(lat * 0.18, 1),
        round(lat * 0.55, 1),
        200,
    )


async def seed_demo(rt: Any) -> None:
    store = rt.store
    if await store.get_setting("demo_seeded"):
        return
    log.info("seeding demo data")
    rng = random.Random(42)
    now = datetime.now(UTC).replace(microsecond=0)
    for sc in SCENARIOS:
        await store.set_tracked(sc.sid, True)
    for sid, name, url, _base in CUSTOM:
        cfg = {
            "id": sid,
            "name": name,
            "category": "custom",
            "probes": [{"kind": "http", "url": url}],
        }
        await store.save_custom_service(sid, name, "custom", cfg, "custom")
    for aid, name, loc, lat, lon in AGENTS:
        if await store.get_agent(aid) is None:
            await store.create_agent(aid, name, loc, lat, lon)
    # Probe history: 24h raw at 2-minute spacing (fast to seed),
    # 7 days of 5-minute rollups and 90 days of daily rollups.
    scen = {s.sid: s for s in SCENARIOS} | {
        sid: Scenario(sid, base) for sid, _n, _u, base in CUSTOM
    }
    rows: list[ProbeResult] = []
    roll: list[dict[str, Any]] = []
    for sid, sc in scen.items():
        bad_days = {d for d in range(1, 91) if rng.random() < (0.05 if sc.mode != "ok" else 0.02)}
        svc = rt.catalog.get(sid)
        n_probes = len(svc.probes) if svc else 1
        for i in range(n_probes):
            kind = svc.probes[i].kind if svc else "http"
            pid = (
                f"{sid}:{kind}:{sum(1 for p in (svc.probes[:i] if svc else []) if p.kind == kind)}"
            )
            t = now - timedelta(hours=24)
            while t <= now:
                rows.append(_result(pid, sc, t, now, rng))
                t += timedelta(minutes=2)
            for d in range(1, 91):
                day = floor_time(now - timedelta(days=d), 86400)
                bad = d in bad_days
                n = 1440
                n_ok = n - (rng.randint(20, 240) if bad else rng.randint(0, 3))
                p50 = sc.base_ms * rng.uniform(0.9, 1.1)
                roll.append(
                    {
                        "probe_id": pid,
                        "agent_id": "",
                        "res": "1d",
                        "bucket": day,
                        "n": n,
                        "n_ok": n_ok,
                        "avg_ms": p50 * 1.05,
                        "p50_ms": p50,
                        "p95_ms": p50 * 1.8,
                    }
                )
            b = floor_time(now - timedelta(days=7), 300)
            while b < now - timedelta(hours=24):
                lat = _latency(sc.base_ms, b, rng)
                roll.append(
                    {
                        "probe_id": pid,
                        "agent_id": "",
                        "res": "5m",
                        "bucket": b,
                        "n": 5,
                        "n_ok": 5,
                        "avg_ms": lat,
                        "p50_ms": lat,
                        "p95_ms": lat * 1.6,
                    }
                )
                b += timedelta(minutes=5)
    await store.insert_results(rows)
    async with store.engine.begin() as c:
        for i in range(0, len(roll), 500):
            await upsert(c, rollups_t, roll[i : i + 500], ["probe_id", "agent_id", "res", "bucket"])
    await store.set_setting("rollup_5m_watermark", floor_time(now, 300).isoformat())
    await store.set_setting("demo_seeded", now.isoformat())


def _snapshot(sc: Scenario, rt: Any, now: datetime, rng: random.Random) -> FeedSnapshot:
    incidents: list[Incident] = []
    # Resolved history for MTTR / incident pages.
    hist_rng = random.Random(hash(sc.sid) & 0xFFFF)
    for k in range(hist_rng.randint(1, 4)):
        start = now - timedelta(days=hist_rng.randint(2, 85), hours=hist_rng.randint(0, 23))
        dur = timedelta(minutes=hist_rng.randint(20, 300))
        incidents.append(
            Incident(
                ext_id=f"demo-{sc.sid}-{k}",
                title=hist_rng.choice(
                    [
                        "Elevated error rates",
                        "Degraded performance for some users",
                        "Login failures",
                        "Delayed notifications",
                        "API latency increase",
                    ]
                ),
                impact=hist_rng.choice(["minor", "major", "minor", "critical"]),
                status="resolved",
                started_at=start,
                resolved_at=start + dur,
                url=None,
                updates=[
                    IncidentUpdate(
                        f"demo-{sc.sid}-{k}-2",
                        "resolved",
                        "This incident has been resolved.",
                        start + dur,
                    ),
                    IncidentUpdate(
                        f"demo-{sc.sid}-{k}-1",
                        "investigating",
                        "We are investigating reports of issues.",
                        start,
                    ),
                ],
            )
        )
    components: list[Component] = []
    if sc.sid == "cloudflare":
        for code, reg in rt.catalog.regions.items():
            if reg.provider != "cloudflare":
                continue
            iata = code.split(":")[1]
            components.append(
                Component(
                    code,
                    f"{reg.name.replace('Cloudflare ', '')} - ({iata})",
                    "degraded" if iata in CF_DOWN else "operational",
                    code,
                )
            )
    if sc.feed != "none":
        kind = "maintenance" if sc.feed == "maintenance" else "incident"
        regions = {"aws": ["aws:us-east-1"], "cloudflare": [f"cf:{c}" for c in CF_DOWN]}.get(
            sc.sid, []
        )
        started = now - timedelta(minutes=47)
        incidents.append(
            Incident(
                ext_id=f"demo-{sc.sid}-active",
                title=sc.title,
                impact=sc.feed,  # type: ignore[arg-type]
                status="in_progress" if kind == "maintenance" else "identified",
                kind=kind,  # type: ignore[arg-type]
                started_at=started,
                scheduled_for=started if kind == "maintenance" else None,
                scheduled_until=now + timedelta(hours=1) if kind == "maintenance" else None,
                regions=regions,
                updates=[
                    IncidentUpdate(
                        f"demo-{sc.sid}-a2",
                        "identified",
                        "The cause has been identified and a fix is being rolled out.",
                        now - timedelta(minutes=12),
                    ),
                    IncidentUpdate(
                        f"demo-{sc.sid}-a1",
                        "investigating",
                        "We are investigating reports of degraded service.",
                        started,
                    ),
                ],
            )
        )
    if sc.sid == "zoom":
        start = now + timedelta(days=2)
        incidents.append(
            Incident(
                "demo-zoom-upcoming",
                "Planned network upgrade",
                "maintenance",
                "scheduled",
                "maintenance",
                scheduled_for=start,
                scheduled_until=start + timedelta(hours=2),
                started_at=start,
            )
        )
    return FeedSnapshot(sc.feed, "Demo feed", components, incidents)  # type: ignore[arg-type]


async def _tick(rt: Any, rng: random.Random, first: bool = False) -> None:
    mon = rt.monitor
    now = datetime.now(UTC)
    scen = {s.sid: s for s in SCENARIOS}
    # Connectivity: always online in the demo.
    from pulse.probes.canary import canary_specs

    specs = canary_specs(["https://1.1.1.1", "https://dns.google", "https://example.com"])
    mon.canary_specs = specs
    mon.canary.update(
        [ProbeResult(s.id, now, True, round(rng.uniform(8, 30), 1)) for s in specs], now
    )
    if first:
        for sc in SCENARIOS:
            if sc.sid in mon.services and mon.services[sc.sid].feed is not None:
                await mon.apply_snapshot(sc.sid, _snapshot(sc, rt, now, rng))
    else:
        for fi in mon.feeds.values():
            fi.last_ok_at = now
    # Agents
    for aid, name, loc, lat, lon in AGENTS:
        mon.agents[aid] = {
            "id": aid,
            "name": name,
            "location": loc,
            "lat": lat,
            "lon": lon,
            "last_seen_at": now,
            "revoked": False,
        }
        await rt.store.update_agent(aid, last_seen_at=now)
    new: list[ProbeResult] = []
    for sid, specs_ in mon.specs.items():
        sc = scen.get(sid, Scenario(sid, 6))
        for i, (aid, *_rest) in enumerate(AGENTS):
            agent_fail = i < sc.agents_fail
            for spec in specs_:
                lat_ms = _latency(sc.base_ms * (1.4 + i * 0.6), now, rng)
                prev = mon.agent_views.get((aid, spec.id))
                fails = (prev.consecutive_failures + 1 if prev else 3) if agent_fail else 0
                mon.agent_views[(aid, spec.id)] = AgentView(
                    now, not agent_fail, None if agent_fail else lat_ms, fails
                )
        for spec in specs_:
            r = _result(spec.id, sc, now, now, rng)
            mon.history.setdefault(spec.id, ProbeHistory()).add(r)
            new.append(r)
    await rt.store.insert_results(new)
    for sid in list(mon.services):
        await mon.evaluate(sid, now)
    if first:  # run twice so flap protection settles into the scenario states
        for sid in list(mon.services):
            await mon.evaluate(sid, now)


class DemoLoop:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        rng = random.Random()
        await _tick(self.rt, rng, first=True)
        while True:
            await asyncio.sleep(20)
            try:
                await _tick(self.rt, rng)
            except Exception:
                log.exception("demo tick failed")

    async def stop(self) -> None:
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


def start_demo_loop(rt: Any) -> DemoLoop:
    return DemoLoop(rt)
