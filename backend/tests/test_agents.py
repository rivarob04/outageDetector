from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pulse.adapters import parse_feed
from pulse.agent import runner as agent_runner
from pulse.agent.signing import (
    SignatureError,
    make_token,
    parse_token,
    sign,
    signed_headers,
    verify,
)
from pulse.config import Settings
from pulse.probes.types import ProbeResult, ProbeSpec
from tests.test_api import rt_of, setup_admin

LIVE = Path(__file__).parent / "fixtures" / "feeds" / "live"


def test_sign_and_verify_roundtrip() -> None:
    token = make_token("hub-key", "abc123")
    headers = signed_headers(token, "POST", "/api/v1/agent/results", b'{"x":1}')
    v = verify("hub-key", headers, "POST", "/api/v1/agent/results", b'{"x":1}')
    assert v.agent_id == "abc123"
    with pytest.raises(SignatureError, match="bad signature"):
        verify("hub-key", headers, "POST", "/api/v1/agent/results", b'{"x":2}')
    with pytest.raises(SignatureError, match="bad signature"):
        verify("other-key", headers, "POST", "/api/v1/agent/results", b'{"x":1}')
    with pytest.raises(SignatureError, match="timestamp"):
        verify(
            "hub-key", headers, "POST", "/api/v1/agent/results", b'{"x":1}', now=time.time() + 900
        )
    with pytest.raises(SignatureError, match="missing"):
        verify("hub-key", {}, "GET", "/", b"")


def test_token_parsing() -> None:
    aid, secret = parse_token(make_token("k", "agent1"))
    assert aid == "agent1" and len(secret) == 64
    with pytest.raises(ValueError):
        parse_token("nope")


async def make_agent(client: httpx.AsyncClient, csrf: str, **body: object) -> dict[str, str]:
    r = await client.post(
        "/api/v1/agents",
        headers={"x-csrf-token": csrf},
        json={"name": "VPS", "lat": 50.1, "lon": 8.7, **body},
    )
    assert r.status_code == 201
    return r.json()


async def signed(
    client: httpx.AsyncClient, token: str, method: str, path: str, payload: dict | None = None
) -> httpx.Response:
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = signed_headers(token, method, path, body)
    return await client.request(
        method, path, content=body or None, headers={**headers, "content-type": "application/json"}
    )


async def test_agent_lifecycle_and_widespread_verdict(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    await client.post("/api/v1/services/steam/track", headers=h)
    await client.post(
        "/api/v1/services",
        headers=h,
        json={"name": "NAS", "probes": [{"kind": "http", "url": "http://192.168.1.5"}]},
    )
    a1 = await make_agent(client, csrf, name="Frankfurt")
    a2 = await make_agent(client, csrf, name="Toronto")
    assert a1["token"].startswith(a1["id"] + ".")

    r = await signed(client, a1["token"], "GET", "/api/v1/agent/probes")
    assert r.status_code == 200
    probe_ids = {p["id"] for p in r.json()["probes"]}
    assert "steam:http:0" in probe_ids
    assert not any(p.startswith("nas:") for p in probe_ids)  # private custom target withheld

    rt = rt_of(client)
    mon = rt.monitor
    now = datetime.now(UTC)
    # Local checks fail...
    for i in range(2):
        for spec in mon.specs["steam"]:
            await mon.record_result(
                "steam", ProbeResult(spec.id, now + timedelta(seconds=i), False, error="timeout")
            )
    # ...and both agents see failures too.
    for token in (a1["token"], a2["token"]):
        for i in range(2):
            payload = {
                "agent": {"version": "test"},
                "results": [
                    ProbeResult(s.id, now + timedelta(seconds=i), False, error="connect").to_dict()
                    for s in mon.specs["steam"]
                ],
            }
            resp = await signed(client, token, "POST", "/api/v1/agent/results", payload)
            assert resp.status_code == 200, resp.text
            assert resp.json()["accepted"] == 3
    await mon.evaluate("steam")
    v = await mon.evaluate("steam")
    assert (v.state, v.scope) == ("major_outage", "widespread")
    detail = (await client.get("/api/v1/services/steam")).json()
    assert {a["name"] for a in detail["agents"]} == {"Frankfurt", "Toronto"}

    agents = (await client.get("/api/v1/agents")).json()
    assert all(a["fresh"] and a["version"] == "test" for a in agents)

    # Revoked agents are rejected and stop counting.
    await client.post(f"/api/v1/agents/{a2['id']}/revoke", headers=h)
    r = await signed(client, a2["token"], "GET", "/api/v1/agent/probes")
    assert r.status_code == 403
    v = await mon.evaluate("steam")
    assert v.scope == "widespread"  # local + Frankfurt still = 2 of 2 points


async def test_agent_replay_and_bad_signature_rejected(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    a = await make_agent(client, csrf)
    headers = signed_headers(a["token"], "GET", "/api/v1/agent/probes")
    assert (await client.get("/api/v1/agent/probes", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/agent/probes", headers=headers)).status_code == 409
    forged = signed_headers(a["token"], "GET", "/api/v1/agent/probes")
    forged["x-pulse-signature"] = sign(
        "0" * 64,
        forged["x-pulse-timestamp"],
        forged["x-pulse-nonce"],
        "GET",
        "/api/v1/agent/probes",
        b"",
    )
    assert (await client.get("/api/v1/agent/probes", headers=forged)).status_code == 401
    other = make_token("different-hub-key", a["id"])
    r = await signed(client, other, "GET", "/api/v1/agent/probes")
    assert r.status_code == 401


async def test_offline_agent_is_not_fresh(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    a = await make_agent(client, csrf)
    r = await signed(
        client, a["token"], "POST", "/api/v1/agent/results", {"offline": True, "results": []}
    )
    assert r.status_code == 200
    agents = (await client.get("/api/v1/agents")).json()
    assert agents[0]["last_seen_at"] and not agents[0]["fresh"]


async def test_map_regions_from_incidents_and_components(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    for sid in ("aws", "cloudflare"):
        await client.post(f"/api/v1/services/{sid}/track", headers=h)
    rt = rt_of(client)
    for sid, f in (("aws", "aws.json"), ("cloudflare", "cloudflare.json")):
        snap = parse_feed((LIVE / f).read_bytes(), rt.catalog.services[sid].feed)
        await rt.monitor.apply_snapshot(sid, snap)
    await make_agent(client, csrf, name="Edge")
    m = (await client.get("/api/v1/map")).json()
    assert not m["replay"]
    regions = {r["id"]: r for r in m["regions"]}
    assert regions["aws:me-central-1"]["state"] == "major_outage"
    assert regions["aws:us-east-1"]["state"] == "operational"
    assert any(r.startswith("cf:") for r in regions)
    assert {"aws", "cloudflare"} <= set(m["providers"])
    assert m["agents"][0]["name"] == "Edge"
    # Replay: an hour ago the AWS events (started months ago) were already active.
    at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    past = (await client.get("/api/v1/map", params={"at": at})).json()
    assert past["replay"]
    assert any(r["id"] == "aws:me-central-1" for r in past["regions"])


async def test_agent_runtime_pushes_to_hub(client: httpx.AsyncClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    csrf = await setup_admin(client)
    await client.post("/api/v1/services/github/track", headers={"x-csrf-token": csrf})
    a = await make_agent(client, csrf)
    app = client.app  # type: ignore[attr-defined]

    async def fake_probe(spec: ProbeSpec, ctx: object) -> ProbeResult:
        return ProbeResult(spec.id, datetime.now(UTC), True, 42.0)

    async def online(specs: object, http: object) -> list[ProbeResult]:
        return [ProbeResult("canary:0", datetime.now(UTC), True, 5.0)]

    monkeypatch.setattr(agent_runner, "run_probe", fake_probe)
    monkeypatch.setattr(agent_runner, "run_canaries", online)
    s = Settings(hub_url="http://hub", agent_token=a["token"], data_dir=Path("/tmp"))
    agent = agent_runner.Agent(s, transport=httpx.ASGITransport(app=app))
    await agent.refresh_probes()
    assert len(agent.probes) == 3
    agent.buffer.extend(await agent.run_round())
    assert await agent.push(offline=False)
    assert not agent.buffer
    mon = rt_of(client).monitor
    assert mon.agent_views[(a["id"], "github:http:0")].latency_ms == 42.0
    await agent.close()


def test_agent_requires_configuration() -> None:
    with pytest.raises(SystemExit):
        agent_runner.Agent(Settings(data_dir=Path("/tmp")))
