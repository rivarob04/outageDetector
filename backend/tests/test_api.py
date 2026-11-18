from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pulse.adapters import parse_feed
from pulse.app import create_app
from pulse.config import Settings
from pulse.probes.types import ProbeResult

LIVE = Path(__file__).parent / "fixtures" / "feeds" / "live"


async def setup_admin(client: httpx.AsyncClient) -> str:
    rt = client.app.state.rt  # type: ignore[attr-defined]
    r = await client.post(
        "/api/v1/auth/setup",
        json={
            "username": "admin",
            "password": "correct horse battery",
            "setup_code": rt.setup_code,
        },
    )
    assert r.status_code == 200, r.text
    return str(r.json()["csrf"])


def rt_of(client: httpx.AsyncClient):  # type: ignore[no-untyped-def]
    return client.app.state.rt  # type: ignore[attr-defined]


async def test_setup_requires_code_and_only_once(client: httpx.AsyncClient) -> None:
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["setup_required"] and me["user"] is None
    bad = await client.post(
        "/api/v1/auth/setup", json={"username": "a", "password": "0123456789", "setup_code": "nope"}
    )
    assert bad.status_code == 403
    await setup_admin(client)
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["user"]["username"] == "admin" and me["csrf"]
    again = await client.post(
        "/api/v1/auth/setup", json={"username": "b", "password": "0123456789", "setup_code": "x"}
    )
    assert again.status_code == 409


async def test_login_logout_rate_limit(client: httpx.AsyncClient) -> None:
    await setup_admin(client)
    await client.post("/api/v1/auth/logout")
    assert (await client.get("/api/v1/auth/me")).json()["user"] is None
    for _ in range(5):
        r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "correct horse battery"}
    )
    assert r.status_code == 429
    from pulse.api.routes import limiter

    limiter._fails.clear()
    r = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "correct horse battery"}
    )
    assert r.status_code == 200 and r.json()["csrf"]


async def test_admin_requires_login_and_csrf(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/services/github/track")).status_code == 401
    csrf = await setup_admin(client)
    assert (await client.post("/api/v1/services/github/track")).status_code == 403
    r = await client.post("/api/v1/services/github/track", headers={"x-csrf-token": csrf})
    assert r.status_code == 200


async def test_track_summary_detail_untrack(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    assert (await client.post("/api/v1/services/nope/track", headers=h)).status_code == 404
    await client.post("/api/v1/services/github/track", headers=h)
    summary = (await client.get("/api/v1/summary")).json()
    [svc] = summary["services"]
    assert svc["id"] == "github" and svc["state"] == "unknown"
    assert len(svc["uptime"]) == 90 and len(svc["sparkline"]) == 48
    detail = (await client.get("/api/v1/services/github")).json()
    assert detail["tracked"] and detail["verified"] is True
    assert [p["kind"] for p in detail["probes"]] == ["http", "http", "dns"]
    cat = (await client.get("/api/v1/catalog")).json()
    assert next(i for i in cat["items"] if i["id"] == "github")["tracked"]
    await client.delete("/api/v1/services/github/track", headers=h)
    assert (await client.get("/api/v1/summary")).json()["services"] == []


async def test_feed_snapshot_incidents_and_resolution(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    await client.post("/api/v1/services/twilio/track", headers={"x-csrf-token": csrf})
    rt = rt_of(client)
    svc = rt.catalog.services["twilio"]
    snap = parse_feed((LIVE / "twilio.json").read_bytes(), svc.feed)
    events: list[str] = []

    async with rt.bus.subscribe() as q:
        change = await rt.monitor.apply_snapshot("twilio", snap)
        while not q.empty():
            events.append((await q.get()).type)
    assert change.new_incidents and "incident" in events

    active = (await client.get("/api/v1/incidents?status=active")).json()
    assert active["total"] == len(snap.active_incidents) + len(snap.active_maintenance)
    upcoming = (await client.get("/api/v1/incidents?status=upcoming")).json()
    assert upcoming["total"] == len([i for i in snap.incidents if i.status == "scheduled"])
    first = active["items"][0]
    assert first["service_name"] == "Twilio" and "updates" in first

    v = await rt.monitor.evaluate("twilio")
    assert v.scope == "provider_confirmed"
    tile = (await client.get("/api/v1/summary")).json()["services"][0]
    assert tile["state"] == v.state and tile["incidents"]

    # Next poll: the page no longer lists them -> they're closed.
    snap.incidents = []
    snap.indicator = "none"
    change2 = await rt.monitor.apply_snapshot("twilio", snap)
    assert change2.resolved
    assert (await client.get("/api/v1/incidents?status=active")).json()["total"] == 0
    stats = (await client.get("/api/v1/incidents/stats")).json()
    assert stats["providers"][0]["service_id"] == "twilio"


async def test_probe_results_drive_verdict_and_transitions(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    await client.post("/api/v1/services/steam/track", headers={"x-csrf-token": csrf})
    rt = rt_of(client)
    mon = rt.monitor
    now = datetime.now(UTC)
    for spec in mon.specs["steam"]:
        await mon.record_result("steam", ProbeResult(spec.id, now, True, 120.0))
    v = await mon.evaluate("steam")
    assert v.state == "operational" and v.scope == "none"
    for i in range(3):  # 2 failures to count + 1 more evaluation to confirm the change
        for spec in mon.specs["steam"]:
            await mon.record_result(
                "steam",
                ProbeResult(spec.id, now + timedelta(seconds=i + 1), False, error="timeout"),
            )
        v = await mon.evaluate("steam")
    assert (v.state, v.scope) == ("major_outage", "local_only")
    detail = (await client.get("/api/v1/services/steam")).json()
    assert detail["state"] == "major_outage"
    assert any("failing" in r for r in detail["reasons"])
    assert [t["to_state"] for t in detail["transitions"]][:2] == ["major_outage", "operational"]
    assert detail["probes"][0]["latest"]["error"] == "timeout"
    lat = (await client.get("/api/v1/services/steam/latency?range=24h")).json()
    assert len(lat["probes"][0]["points"]) == 4


async def test_custom_service_crud_and_ssrf_flag(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    body = {"name": "My NAS", "probes": [{"kind": "http", "url": "http://192.168.1.10:5000"}]}
    r = await client.post("/api/v1/services", json=body, headers=h)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert sid == "my-nas"
    rt = rt_of(client)
    assert rt.monitor.specs[sid][0].guarded
    r2 = await client.post("/api/v1/services", json=body, headers=h)
    assert r2.json()["id"] == "my-nas-2"
    bad = await client.post("/api/v1/services", json={"name": "x", "probes": []}, headers=h)
    assert bad.status_code == 422
    upd = await client.put(f"/api/v1/services/{sid}", headers=h, json={**body, "name": "NAS"})
    assert upd.status_code == 200
    assert (await client.get(f"/api/v1/services/{sid}")).json()["name"] == "NAS"
    assert (await client.put("/api/v1/services/github", headers=h, json=body)).status_code == 409
    assert (await client.delete("/api/v1/services/github", headers=h)).status_code == 409
    assert (await client.delete(f"/api/v1/services/{sid}", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/services/{sid}")).status_code == 404


async def test_export_import_roundtrip(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    await client.post("/api/v1/services/github/track", headers=h)
    await client.post(
        "/api/v1/services",
        headers=h,
        json={"name": "Blog", "probes": [{"kind": "http", "url": "https://example.com"}]},
    )
    exp = (await client.get("/api/v1/config/export", headers=h)).json()
    assert exp["track"] == ["github"] and exp["custom"][0]["id"] == "blog"
    imp = {**exp, "track": ["aws", "cloudflare"], "replace": True}
    r = await client.post("/api/v1/config/import", headers=h, json=imp)
    assert r.status_code == 200
    ids = {s["id"] for s in (await client.get("/api/v1/summary")).json()["services"]}
    assert ids == {"aws", "cloudflare", "blog"}
    bad = await client.post("/api/v1/config/import", headers=h, json={"track": ["nope"]})
    assert bad.status_code == 422


async def test_settings_validation_and_apply(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    s = (await client.get("/api/v1/settings", headers=h)).json()
    assert s["editable"]["fail_threshold"] == 2
    assert (
        await client.put("/api/v1/settings", headers=h, json={"secret_key": "x"})
    ).status_code == 422
    assert (
        await client.put("/api/v1/settings", headers=h, json={"fail_threshold": 99})
    ).status_code == 422
    r = await client.put(
        "/api/v1/settings",
        headers=h,
        json={"fail_threshold": 3, "canary_targets": "https://a.example"},
    )
    assert r.status_code == 200
    rt = rt_of(client)
    assert rt.monitor.vcfg.fail_threshold == 3
    assert [c.url for c in rt.monitor.canary_specs] == ["https://a.example"]


async def test_private_view_requires_login(tmp_path: Path) -> None:
    s = Settings(
        data_dir=tmp_path,
        secret_key="k",
        log_json=False,
        public_view=False,
        admin_username="admin",
        admin_password="correct horse battery",
    )
    app = create_app(s, start_background=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        assert (await c.get("/api/v1/summary")).status_code == 401
        assert (await c.get("/healthz")).status_code == 200
        r = await c.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct horse battery"},
        )
        assert r.status_code == 200
        assert (await c.get("/api/v1/summary")).status_code == 200


async def test_trusted_header_auth(tmp_path: Path) -> None:
    s = Settings(
        data_dir=tmp_path,
        secret_key="k",
        log_json=False,
        trusted_auth_header="Remote-User",
        trusted_proxies="127.0.0.1/32",
    )
    app = create_app(s, start_background=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            me = (await c.get("/api/v1/auth/me", headers={"Remote-User": "alice"})).json()
            assert me["user"] == {"username": "alice", "via": "header"}
            assert not me["setup_required"]
            r = await c.post("/api/v1/services/github/track", headers={"Remote-User": "alice"})
            assert r.status_code == 200  # header auth needs no CSRF token
        untrusted = httpx.ASGITransport(app=app, client=("203.0.113.9", 1234))
        async with httpx.AsyncClient(transport=untrusted, base_url="http://t") as c:
            me = (await c.get("/api/v1/auth/me", headers={"Remote-User": "alice"})).json()
            assert me["user"] is None


async def test_config_file_seeds_and_locks(tmp_path: Path) -> None:
    (tmp_path / "pulse.yaml").write_text(
        "track: [github]\n"
        "custom:\n  - id: router\n    name: Router\n    category: network\n"
        "    probes: [{kind: tcp, host: 192.168.1.1, port: 443}]\n"
        "settings: {fail_threshold: 4}\n"
    )
    s = Settings(
        data_dir=tmp_path,
        secret_key="k",
        log_json=False,
        admin_username="admin",
        admin_password="correct horse battery",
    )
    app = create_app(s, start_background=False)
    async with app.router.lifespan_context(app):
        rt = app.state.rt
        assert set(rt.monitor.services) == {"github", "router"}
        assert rt.settings.fail_threshold == 4 and "fail_threshold" in rt.config_locked
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            csrf = (
                await c.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": "correct horse battery"},
                )
            ).json()["csrf"]
            r = await c.put(
                "/api/v1/settings", headers={"x-csrf-token": csrf}, json={"fail_threshold": 2}
            )
            assert r.status_code == 409
            r = await c.put(
                "/api/v1/services/router",
                headers={"x-csrf-token": csrf},
                json={"name": "x", "probes": [{"kind": "dns", "host": "a.b"}]},
            )
            assert r.status_code == 409


async def test_rollups_and_retention(client: httpx.AsyncClient) -> None:
    rt = rt_of(client)
    store = rt.store
    from pulse.db.store import floor_time

    base = floor_time(datetime.now(UTC) - timedelta(days=9), 300)
    results = [
        ProbeResult("x:http:0", base + timedelta(minutes=i), i % 10 != 0, 100.0 + i)
        for i in range(60)
    ]
    await store.insert_results(results)
    await store.set_setting("rollup_5m_watermark", (base - timedelta(hours=1)).isoformat())
    n = await store.rollup_5m()
    assert n == 12
    five = await store.rollups(["x:http:0"], "5m", base - timedelta(hours=1))
    assert sum(r["n"] for r in five) == 60
    assert sum(r["n_ok"] for r in five) == 54
    days = await store.rollups(["x:http:0"], "1d", base - timedelta(days=1))
    assert sum(r["n"] for r in days) == 60
    counts = await store.retention(raw_days=7, r5m_days=90, incident_days=365)
    assert counts["probe_results"] == 60
    assert await store.rollup_5m() == 0  # watermark advanced


async def test_sse_stream_emits_events(client: httpx.AsyncClient) -> None:
    from pulse.api.routes import events

    rt = rt_of(client)

    class FakeRequest:
        app = client.app  # type: ignore[attr-defined]

        async def is_disconnected(self) -> bool:
            return False

    resp = await events(FakeRequest())  # type: ignore[arg-type]
    it = resp.body_iterator
    assert (await it.__anext__()).startswith("retry:")
    nxt = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0.01)
    rt.bus.publish("state", {"service_id": "github", "state": "major_outage"})
    chunk = await asyncio.wait_for(nxt, 2)
    assert chunk.startswith("event: state")
    assert json.loads(chunk.split("data: ", 1)[1])["state"] == "major_outage"
    await it.aclose()  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", ["/api/v1/services/nope", "/api/v1/services/nope/latency"])
async def test_404s(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 404
