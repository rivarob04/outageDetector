from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from pulse.adapters import parse_feed
from pulse.integrations.mqtt import discovery_messages
from pulse.notify import notifier as notifier_mod
from pulse.notify.deliver import mask_url, send_webhook, webhook_signature
from pulse.notify.rules import Rule, decide, format_state, in_quiet_hours
from pulse.probes.types import ProbeResult
from tests.test_api import rt_of, setup_admin

LIVE = Path(__file__).parent / "fixtures" / "feeds" / "live"


def ev(
    state: str,
    scope: str,
    prev_state: str | None = "operational",
    prev_scope: str | None = "none",
    **kw: Any,
) -> dict[str, Any]:
    return {
        "service_id": "github",
        "service_name": "GitHub",
        "category": "devtools",
        "state": state,
        "scope": scope,
        "prev_state": prev_state,
        "prev_scope": prev_scope,
        "reasons": ["Provider confirmed: Git is down"],
        "advisories": [],
        **kw,
    }


@pytest.mark.parametrize(
    ("rule", "event", "expected"),
    [
        (Rule(1, 1), ev("major_outage", "provider_confirmed"), "problem"),
        (Rule(1, 1), ev("degraded", "provider_confirmed"), None),  # below min_state
        (Rule(1, 1, min_state="degraded"), ev("degraded", "provider_confirmed"), "problem"),
        (Rule(1, 1), ev("major_outage", "local_only"), None),  # scope not selected
        (Rule(1, 1, scopes=["local_only"]), ev("major_outage", "local_only"), "problem"),
        (Rule(1, 1), ev("major_outage", "widespread", "partial_outage", "widespread"), "problem"),
        (Rule(1, 1), ev("partial_outage", "widespread", "major_outage", "widespread"), None),
        (Rule(1, 1), ev("operational", "none", "major_outage", "provider_confirmed"), "recovery"),
        (Rule(1, 1, recovery=False), ev("operational", "none", "major_outage", "widespread"), None),
        (Rule(1, 1, services=["aws"]), ev("major_outage", "provider_confirmed"), None),
        (Rule(1, 1, categories=["devtools"]), ev("major_outage", "provider_confirmed"), "problem"),
        (Rule(1, 1, categories=["cloud"]), ev("major_outage", "provider_confirmed"), None),
        (Rule(1, 1, enabled=False), ev("major_outage", "provider_confirmed"), None),
        # local_only -> widespread escalation of scope at the same state counts as new
        (Rule(1, 1), ev("major_outage", "widespread", "major_outage", "local_only"), "problem"),
    ],
)
def test_decide(rule: Rule, event: dict[str, Any], expected: str | None) -> None:
    assert decide(rule, event) == expected


def test_quiet_hours_wrap_midnight_and_timezones() -> None:
    r = Rule(1, 1, quiet_start="22:00", quiet_end="07:00", tz="America/Toronto")
    assert in_quiet_hours(r, datetime(2026, 11, 18, 4, 0, tzinfo=UTC))  # 23:00 Toronto
    assert not in_quiet_hours(r, datetime(2026, 11, 18, 17, 0, tzinfo=UTC))  # 12:00
    day = Rule(1, 1, quiet_start="09:00", quiet_end="17:00")
    assert in_quiet_hours(day, datetime(2026, 1, 1, 10, tzinfo=UTC))
    assert not in_quiet_hours(Rule(1, 1), datetime(2026, 1, 1, 3, tzinfo=UTC))


def test_format_uses_shapes_and_links() -> None:
    title, body = format_state(ev("major_outage", "widespread"), "https://pulse.example")
    assert title == "■ GitHub: Major outage (failing from several places)"
    assert "Git is down" in body and body.endswith("https://pulse.example/service/github")
    title, _ = format_state(ev("operational", "none"), "")
    assert title == "● GitHub recovered"


def test_mask_url() -> None:
    assert mask_url("discord://1234/secret-token") == "discord://1234/…"
    assert mask_url("ntfys://user:pass@ntfy.example.com/topic") == "ntfys://ntfy.example.com/…"
    assert mask_url("nonsense") == "***"


async def test_webhook_is_signed() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await send_webhook("https://hook.example/x", "s3cret", {"event": "problem"}, c)
    req = seen[0]
    ts = req.headers["x-pulse-timestamp"]
    assert req.headers["x-pulse-signature"] == webhook_signature("s3cret", ts, req.content)
    assert json.loads(req.content)["event"] == "problem"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    async def fake(channel: dict, title: str, body: str, payload: dict, client: Any = None) -> None:
        out.append((channel["name"], title))

    monkeypatch.setattr(notifier_mod, "deliver", fake)
    return out


async def test_notifier_problem_recovery_cooldown_and_offline(
    client: httpx.AsyncClient, sent: list[tuple[str, str]]
) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    r = await client.post(
        "/api/v1/alerts/channels",
        headers=h,
        json={"name": "phone", "kind": "apprise", "url": "ntfy://topic"},
    )
    assert r.status_code == 201
    rt = rt_of(client)
    n = rt.notifier
    now = datetime.now(UTC)
    await n.on_state(ev("major_outage", "provider_confirmed"), now)
    assert sent == [("phone", "■ GitHub: Major outage (confirmed by the provider)")]
    # flapping within cooldown: no repeat
    await n.on_state(ev("major_outage", "provider_confirmed"), now + timedelta(seconds=60))
    assert len(sent) == 1
    await n.on_state(ev("operational", "none", "major_outage", "provider_confirmed"), now)
    assert sent[-1][1] == "● GitHub recovered"
    # recovery of something never alerted is silent
    await n.on_state(
        ev(
            "operational",
            "none",
            "degraded",
            "provider_confirmed",
            service_id="aws",
            service_name="AWS",
        ),
        now,
    )
    assert len(sent) == 2
    # offline: paused, then one summary
    rt.monitor.canary.offline = True
    await n.on_connectivity({"offline": True}, now)
    await n.on_state(ev("major_outage", "widespread", service_id="x", service_name="X"), now)
    assert len(sent) == 2
    rt.monitor.canary.offline = False
    await n.on_connectivity({"offline": False}, now + timedelta(minutes=12))
    assert sent[-1][1] == "○ You were offline for 12 min"
    log = (await client.get("/api/v1/alerts", headers=h)).json()["log"]
    assert len(log) == 3 and all(entry["ok"] for entry in log)


async def test_alerts_api_rules_test_and_masking(
    client: httpx.AsyncClient, sent: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    r = await client.post(
        "/api/v1/alerts/channels",
        headers=h,
        json={"name": "hook", "kind": "webhook", "url": "https://hook.example/abc/secret"},
    )
    body = r.json()
    assert len(body["webhook_secret"]) == 48
    ov = (await client.get("/api/v1/alerts", headers=h)).json()
    assert ov["channels"][0]["url"] == "https://hook.example/…"
    rule = ov["rules"][0]
    assert rule["min_state"] == "partial_outage" and rule["scopes"] == [
        "provider_confirmed",
        "widespread",
    ]
    bad = await client.put(
        f"/api/v1/alerts/rules/{rule['id']}",
        headers=h,
        json={"channel_id": body["id"], "tz": "Mars/Base", "quiet_start": "25:00"},
    )
    assert bad.status_code == 422
    ok = await client.put(
        f"/api/v1/alerts/rules/{rule['id']}",
        headers=h,
        json={
            "channel_id": body["id"],
            "tz": "Europe/Berlin",
            "quiet_start": "22:00",
            "quiet_end": "07:00",
            "scopes": ["local_only"],
            "digest": True,
        },
    )
    assert ok.status_code == 200
    from pulse.api import notify as notify_api

    async def fake_deliver(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(notify_api, "deliver", fake_deliver)
    assert (
        await client.post(f"/api/v1/alerts/channels/{body['id']}/test", headers=h)
    ).status_code == 200
    await client.delete(f"/api/v1/alerts/channels/{body['id']}", headers=h)
    ov = (await client.get("/api/v1/alerts", headers=h)).json()
    assert ov["channels"] == [] and ov["rules"] == []


async def test_digest(client: httpx.AsyncClient, sent: list[tuple[str, str]]) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    await client.post("/api/v1/services/github/track", headers=h)
    cid = (
        await client.post(
            "/api/v1/alerts/channels", headers=h, json={"name": "mail", "url": "mailto://x"}
        )
    ).json()["id"]
    rule = (await client.get("/api/v1/alerts", headers=h)).json()["rules"][0]
    await client.put(
        f"/api/v1/alerts/rules/{rule['id']}",
        headers=h,
        json={"channel_id": cid, "digest": True, "tz": "Pacific/Kiritimati"},
    )
    rt = rt_of(client)
    title, body = await rt.notifier.build_digest(Rule(1, cid), datetime.now(UTC))
    assert title.startswith("Pulse daily digest") and "Right now" in body
    await rt.notifier.digest_job()
    await rt.notifier.digest_job()  # at most once a day
    digests = [t for _c, t in sent if t.startswith("Pulse daily digest")]
    assert len(digests) <= 1


async def test_metrics_feeds_badges_status_page(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    for sid in ("github", "twilio"):
        await client.post(f"/api/v1/services/{sid}/track", headers=h)
    rt = rt_of(client)
    snap = parse_feed((LIVE / "twilio.json").read_bytes(), rt.catalog.services["twilio"].feed)
    await rt.monitor.apply_snapshot("twilio", snap)
    for spec in rt.monitor.specs["github"]:
        await rt.monitor.record_result(
            "github", ProbeResult(spec.id, datetime.now(UTC), True, 88.0)
        )
    await rt.monitor.evaluate("github")
    await rt.monitor.evaluate("twilio")

    m = (await client.get("/metrics")).text
    assert 'pulse_service_state{service="github",name="GitHub",category="devtools"} 0' in m
    assert 'pulse_probe_latency_ms{service="github",probe="github:http:0",kind="http"} 88.0' in m
    assert "# TYPE pulse_service_up gauge" in m and "pulse_local_network_offline 0" in m

    atom = await client.get("/feed/incidents.atom")
    assert atom.headers["content-type"].startswith("application/atom+xml")
    assert "<entry>" in atom.text and "Twilio:" in atom.text
    ics = await client.get("/feed/maintenance.ics")
    assert ics.text.startswith("BEGIN:VCALENDAR") and "BEGIN:VEVENT" in ics.text
    badge = await client.get("/badge/github.svg")
    assert badge.headers["content-type"] == "image/svg+xml" and "operational" in badge.text
    assert (await client.get("/badge/nope.svg")).status_code == 404

    assert (await client.get("/status")).status_code == 404
    r = await client.put(
        "/api/v1/status-page",
        headers=h,
        json={"enabled": True, "title": "Family status", "services": ["github", "twilio"]},
    )
    assert r.status_code == 200
    page = await client.get("/status")
    assert page.status_code == 200 and "Family status" in page.text and "GitHub" in page.text
    data = (await client.get("/status.json")).json()
    assert [s["id"] for s in data["services"]] == ["github", "twilio"]
    bad = await client.put(
        "/api/v1/status-page", headers=h, json={"enabled": True, "services": ["aws"]}
    )
    assert bad.status_code == 422


async def test_status_page_hides_local_only_problems(client: httpx.AsyncClient) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    await client.post("/api/v1/services/steam/track", headers=h)
    rt = rt_of(client)
    now = datetime.now(UTC)
    for i in range(3):
        for spec in rt.monitor.specs["steam"]:
            await rt.monitor.record_result(
                "steam", ProbeResult(spec.id, now + timedelta(seconds=i), False, error="timeout")
            )
        await rt.monitor.evaluate("steam")
    assert rt.monitor.verdicts["steam"].scope == "local_only"
    await client.put(
        "/api/v1/status-page", headers=h, json={"enabled": True, "services": ["steam"]}
    )
    assert (await client.get("/status.json")).json()["services"][0]["state"] == "operational"


async def test_metrics_token(tmp_path: Path) -> None:
    from pulse.app import create_app
    from pulse.config import Settings

    s = Settings(data_dir=tmp_path, secret_key="k", log_json=False, metrics_token="t0k")
    app = create_app(s, start_background=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        assert (await c.get("/metrics")).status_code == 401
        assert (await c.get("/metrics", headers={"Authorization": "Bearer t0k"})).status_code == 200
        assert (await c.get("/metrics?token=t0k")).status_code == 200


def test_mqtt_discovery_payloads() -> None:
    msgs = dict(discovery_messages("homeassistant", "pulse", "github", "GitHub"))
    state = msgs["homeassistant/sensor/pulse_github_state/config"]
    assert state["state_topic"] == "pulse/github/state"
    assert state["device"]["identifiers"] == ["pulse_github"]
    assert "major_outage" in state["options"]
    problem = msgs["homeassistant/binary_sensor/pulse_github_problem/config"]
    assert problem["device_class"] == "problem"
    assert all(m["availability_topic"] == "pulse/status" for m in msgs.values())
