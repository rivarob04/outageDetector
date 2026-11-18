from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import update

from pulse.adapters import parse_feed
from pulse.db.store import floor_time, upsert
from pulse.db.tables import incidents_t, rollups_t
from pulse.probes.types import ProbeResult
from pulse.signals import chatter as chatter_mod
from pulse.signals.anomaly import AnomalyDetector, ewma, judge, robust
from pulse.signals.chatter import Chatter, is_spike
from tests.test_api import rt_of, setup_admin

LIVE = Path(__file__).parent / "fixtures" / "feeds" / "live"


async def track(client: httpx.AsyncClient, *ids: str) -> None:
    csrf = await setup_admin(client)
    for sid in ids:
        r = await client.post(f"/api/v1/services/{sid}/track", headers={"x-csrf-token": csrf})
        assert r.status_code == 200


async def ok_results(rt, sid: str, latency: float = 80.0) -> None:  # type: ignore[no-untyped-def]
    for spec in rt.monitor.specs[sid]:
        await rt.monitor.record_result(sid, ProbeResult(spec.id, datetime.now(UTC), True, latency))


def aws_snapshot_in(rt, region: str):  # type: ignore[no-untyped-def]
    """The real AWS payload, re-pointed at another region (derived fixture)."""
    raw = json.loads((LIVE / "aws.json").read_bytes().decode("utf-16"))
    for e in raw:
        e["arn"] = e["arn"].replace("me-central-1", region).replace("me-south-1", region)
    return parse_feed(json.dumps(raw).encode(), rt.catalog.services["aws"].feed)


async def test_region_dependency_advisory(client: httpx.AsyncClient) -> None:
    await track(client, "aws", "vercel")
    rt = rt_of(client)
    await ok_results(rt, "vercel")
    v = await rt.monitor.evaluate("vercel")
    assert v.advisories == ()
    await rt.monitor.apply_snapshot("aws", aws_snapshot_in(rt, "us-east-1"))
    v = await rt.monitor.evaluate("vercel")
    assert v.state == "operational"  # advisories never change the state
    assert v.advisories == ("Possibly affected: AWS N. Virginia (us-east-1) is having problems.",)
    tile = next(
        s for s in (await client.get("/api/v1/summary")).json()["services"] if s["id"] == "vercel"
    )
    assert tile["advisories"]


async def test_region_dependency_ignores_other_regions(client: httpx.AsyncClient) -> None:
    await track(client, "aws", "vercel")
    rt = rt_of(client)
    await ok_results(rt, "vercel")
    await rt.monitor.apply_snapshot("aws", aws_snapshot_in(rt, "eu-west-1"))
    assert (await rt.monitor.evaluate("vercel")).advisories == ()


async def test_whole_service_dependency(client: httpx.AsyncClient) -> None:
    await track(client, "aws", "heroku")
    rt = rt_of(client)
    await rt.monitor.apply_snapshot("aws", aws_snapshot_in(rt, "us-west-2"))
    await rt.monitor.evaluate("aws")
    assert rt.monitor.verdicts["aws"].state == "major_outage"
    v = await rt.monitor.evaluate("heroku")
    assert "Possibly affected: Amazon Web Services has a major outage." in v.advisories


async def test_untracked_upstream_gives_no_advisory(client: httpx.AsyncClient) -> None:
    await track(client, "heroku")
    rt = rt_of(client)
    await ok_results(rt, "heroku")
    assert (await rt.monitor.evaluate("heroku")).advisories == ()


def test_robust_stats_and_judge() -> None:
    b = robust([100, 102, 98, 101, 99, 100, 250])
    assert b and b.median == 100 and b.mad == 1
    assert ewma([100, 100, 400]) == pytest.approx(220)
    slow, ratio = judge(450, b, 3.0, 300)
    assert slow and ratio == 4.5
    assert not judge(250, b, 3.0, 300)[0]  # high z, but below factor and floor
    tiny = robust([5.0] * 30)
    assert tiny and not judge(60, tiny, 3.0, 300)[0]  # 12x but only 55 ms slower


async def test_anomaly_detector_hourly_baseline(client: httpx.AsyncClient) -> None:
    await track(client, "github")
    rt = rt_of(client)
    rt.settings.latency_min_ms = 100
    pid = "github:http:0"
    now = datetime.now(UTC)
    rows = []
    for d in range(1, 8):
        for m in range(0, 60, 5):
            b = floor_time(now - timedelta(days=d), 3600) + timedelta(minutes=m)
            rows.append(
                {
                    "probe_id": pid,
                    "agent_id": "",
                    "res": "5m",
                    "bucket": b,
                    "n": 5,
                    "n_ok": 5,
                    "avg_ms": 100.0 + m % 3,
                    "p50_ms": 100.0 + m % 3,
                    "p95_ms": 150.0,
                }
            )
    async with rt.store.engine.begin() as c:
        await upsert(c, rollups_t, rows, ["probe_id", "agent_id", "res", "bucket"])
    det = AnomalyDetector(rt)
    await det.refresh()
    assert now.hour in det.hourly[pid]
    normal = [((now - timedelta(minutes=6 - i)).timestamp(), 100.0) for i in range(6)]
    assert det(pid, normal, 100.0) == (False, None, None)
    slow = [((now - timedelta(minutes=6 - i)).timestamp(), 600.0) for i in range(6)]
    flag, ratio, basis = det(pid, slow, 600.0)
    assert flag and ratio == pytest.approx(6.0, rel=0.05) and basis == "for this hour"
    spike = [*normal[:-1], (now.timestamp(), 900.0)]
    assert det(pid, spike, 900.0)[0] is False  # one slow request isn't enough


async def test_anomaly_fallback_without_hourly_history(client: httpx.AsyncClient) -> None:
    await track(client, "github")
    rt = rt_of(client)
    det = AnomalyDetector(rt)
    now = datetime.now(UTC)
    series = [((now - timedelta(minutes=200 - i)).timestamp(), 100.0 + (i % 5)) for i in range(194)]
    series += [((now - timedelta(minutes=6 - i)).timestamp(), 900.0) for i in range(6)]
    flag, ratio, basis = det("github:http:0", series, 900.0)
    assert flag and basis is None and ratio and ratio > 8
    assert det("github:http:0", series[-10:], 900.0) == (False, None, None)  # too little history


async def test_anomaly_reason_reaches_verdict(client: httpx.AsyncClient) -> None:
    await track(client, "discord")
    rt = rt_of(client)
    mon = rt.monitor
    assert mon.anomaly is not None  # feature on by default
    now = datetime.now(UTC)
    for i in range(200):
        t = now - timedelta(minutes=200 - i)
        lat = 900.0 if i >= 194 else 80.0 + (i % 4)
        for spec in mon.specs["discord"]:
            await mon.record_result("discord", ProbeResult(spec.id, t, True, lat))
    v = await mon.evaluate("discord")
    v = await mon.evaluate("discord")
    assert v.state == "degraded" and any("normal level" in r for r in v.reasons)


def test_is_spike() -> None:
    assert is_spike(12, [1, 0, 2, 1])[0]
    assert not is_spike(2, [0, 0, 0])[0]  # too few posts
    assert not is_spike(12, [10, 9, 11])[0]  # it's always noisy


async def test_chatter_job_with_fake_sources(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await track(client, "github")
    rt = rt_of(client)
    counts = iter([0, 0, 0, 0, 9, 9])

    async def fake_hn(client, phrase, since):  # type: ignore[no-untyped-def]
        return next(counts)

    async def broken(client, phrase, since):  # type: ignore[no-untyped-def]
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(chatter_mod, "SOURCES", {"hn": fake_hn, "reddit": broken})
    ch = Chatter(rt)
    assert ch.phrases("github") == ["github down", "github outage"]
    await ch.job()
    assert ch.advisories("github") == [] and "reddit" in ch.failures
    # Move the first bucket into the past so it counts as history.
    from pulse.db.tables import chatter_t

    async with rt.store.engine.begin() as c:
        await c.execute(
            update(chatter_t).values(
                bucket=floor_time(datetime.now(UTC), 600) - timedelta(minutes=20)
            )
        )
    await ch.job()
    await ch.job()
    adv = ch.advisories("github")
    assert adv and adv[0].startswith("Chatter spike: 18 public posts")


async def test_lead_time_stats(client: httpx.AsyncClient) -> None:
    await track(client, "twilio")
    rt = rt_of(client)
    store = rt.store
    t_wide = datetime.now(UTC) - timedelta(days=2)
    await store.save_state(
        "twilio",
        {
            "state": "major_outage",
            "scope": "widespread",
            "reasons": [],
            "advisories": [],
            "since": t_wide,
            "pending_state": None,
            "pending_scope": None,
            "pending_count": 0,
        },
        transition=True,
        prev={"state": "operational", "scope": "none"},
    )
    t_old = datetime.now(UTC) - timedelta(days=5)
    await store.save_state(
        "twilio",
        {
            "state": "partial_outage",
            "scope": "widespread",
            "reasons": [],
            "advisories": [],
            "since": t_old,
            "pending_state": None,
            "pending_scope": None,
            "pending_count": 0,
        },
        transition=True,
        prev={"state": "operational", "scope": "none"},
    )
    snap = parse_feed((LIVE / "twilio.json").read_bytes(), rt.catalog.services["twilio"].feed)
    snap.incidents = snap.incidents[:1]
    await rt.monitor.apply_snapshot("twilio", snap)
    async with store.engine.begin() as c:
        await c.execute(
            update(incidents_t).values(
                first_seen_at=t_wide + timedelta(minutes=20), started_at=t_wide, kind="incident"
            )
        )
    stats = (await client.get("/api/v1/incidents/stats?days=30")).json()["providers"]
    tw = next(p for p in stats if p["service_id"] == "twilio")
    assert tw["lead"]["confirmed"] == 1 and tw["lead"]["unconfirmed"] == 1
    assert tw["lead"]["median_lead_s"] == pytest.approx(1200, abs=1)
