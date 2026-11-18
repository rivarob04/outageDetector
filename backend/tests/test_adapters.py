"""Adapter contract tests against payloads recorded live by `pulse catalog verify`."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from pulse.adapters import parse_feed
from pulse.adapters import rss as rss_adapter
from pulse.adapters.base import IMPACT_RANK, FeedParseError, load_json
from pulse.catalog.loader import load_catalog
from pulse.catalog.schema import FeedConfig
from pulse.http import PoliteClient

ROOT = Path(__file__).resolve().parents[2]
LIVE = Path(__file__).parent / "fixtures" / "feeds" / "live"
CATALOG = load_catalog(ROOT / "catalog")


def fixture(service_id: str) -> bytes:
    matches = list(LIVE.glob(f"{service_id}.*"))
    assert matches, f"no fixture for {service_id}"
    return matches[0].read_bytes()


def feed_of(service_id: str) -> FeedConfig:
    svc = CATALOG.services[service_id]
    assert svc.feed is not None
    return svc.feed


RECORDED = sorted(
    sid
    for sid in {p.stem for p in LIVE.iterdir()}
    if sid in CATALOG.services and CATALOG.services[sid].feed is not None
)


@pytest.mark.parametrize("service_id", RECORDED)
def test_every_recorded_feed_parses(service_id: str) -> None:
    snap = parse_feed(fixture(service_id), feed_of(service_id))
    assert snap.indicator in IMPACT_RANK
    for inc in snap.incidents:
        assert inc.ext_id
        assert inc.title
        assert inc.kind in ("incident", "maintenance")
    for comp in snap.components:
        assert comp.ext_id and comp.name


def test_catalog_valid_and_mostly_verified() -> None:
    assert len(CATALOG.services) >= 35
    verified = [s for s in CATALOG.services if CATALOG.is_verified(s)]
    assert len(verified) >= 35


def test_statuspage_indicator_matches_payload() -> None:
    raw = json.loads(fixture("cloudflare"))
    snap = parse_feed(fixture("cloudflare"), feed_of("cloudflare"))
    assert snap.indicator == raw["status"]["indicator"]
    assert len(snap.active_incidents) == len(
        [i for i in raw["incidents"] if i["status"] != "resolved"]
    )


def test_cloudflare_components_map_to_regions() -> None:
    snap = parse_feed(fixture("cloudflare"), feed_of("cloudflare"))
    regions = {c.region for c in snap.components if c.region}
    assert len(regions) > 50
    assert all(r.startswith("cf:") and len(r) == 6 for r in regions)
    known = {r for r in regions if r in CATALOG.regions}
    assert "cf:YYZ" in known


def test_statuspage_component_filter_recomputes_indicator() -> None:
    feed = feed_of("twilio").model_copy(update={"components_include": ["^NOPE$"]})
    snap = parse_feed(fixture("twilio"), feed)
    assert snap.components == []
    # Incidents tied to filtered-out components are dropped, so the page-wide indicator is too.
    assert all(not i.component_ids for i in snap.incidents)


def test_statuspage_rejects_wrong_shape() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b'{"hello": 1}', feed_of("github"))


def test_statuspage_incident_updates_are_plain_text() -> None:
    snap = parse_feed(fixture("twilio"), feed_of("twilio"))
    updates = [u for i in snap.incidents for u in i.updates]
    assert updates
    assert not any("<p>" in u.body for u in updates)


def test_aws_utf16_events() -> None:
    raw = fixture("aws")
    assert raw[:2] == b"\xfe\xff"
    snap = parse_feed(raw, feed_of("aws"))
    assert snap.indicator == "critical"
    assert {r for i in snap.incidents for r in i.regions} == {"aws:me-central-1", "aws:me-south-1"}
    assert all(r in CATALOG.regions for i in snap.incidents for r in i.regions)
    first = snap.incidents[0]
    assert first.updates[0].created_at >= first.updates[-1].created_at  # newest first


def test_aws_empty_list_is_operational() -> None:
    snap = parse_feed("[]".encode("utf-16"), feed_of("aws"))
    assert snap.indicator == "none"
    assert snap.incidents == []


def test_gcp_resolved_history_and_regions() -> None:
    snap = parse_feed(fixture("google-cloud"), feed_of("google-cloud"))
    assert snap.indicator == "none"
    assert snap.incidents and all(not i.active for i in snap.incidents)
    regions = {r for i in snap.incidents for r in i.regions}
    assert "gcp:europe-west2" in regions


def test_gcp_active_incident_derived_from_real_payload() -> None:
    data = load_json(fixture("google-cloud"))
    item = copy.deepcopy(data[0])
    item["end"] = None
    item["status_impact"] = "SERVICE_OUTAGE"
    item["currently_affected_locations"] = [{"title": "Iowa (us-central1)", "id": "us-central1"}]
    snap = parse_feed(json.dumps([item]).encode(), feed_of("google-cloud"))
    assert snap.indicator == "critical"
    assert snap.active_incidents[0].regions == ["gcp:us-central1"]


def test_workspace_uses_shared_schema_without_regions() -> None:
    snap = parse_feed(fixture("google"), feed_of("google"))
    assert snap.incidents
    assert all(i.regions == [] for i in snap.incidents)
    assert all(
        i.url and i.url.startswith("https://www.google.com/appsstatus/") for i in snap.incidents
    )


def test_slack_ok() -> None:
    snap = parse_feed(fixture("slack"), feed_of("slack"))
    assert snap.indicator == "none"


def test_slack_active_incident_derived_from_history() -> None:
    history = json.loads((LIVE.parent / "slack-history.json").read_bytes())
    inc = dict(history[0], status="active", type="outage")
    payload = {"status": "active", "active_incidents": [inc]}
    snap = parse_feed(json.dumps(payload).encode(), feed_of("slack"))
    assert snap.indicator == "critical"
    assert snap.active_incidents[0].updates[0].body
    assert "<p>" not in snap.active_incidents[0].updates[0].body


def test_apple_filters_components_and_dedupes_events() -> None:
    snap = parse_feed(fixture("apple"), feed_of("apple"))
    names = {c.name for c in snap.components}
    assert "App Store" in names
    assert all(
        any(k.lower() in n.lower() for k in feed_of("apple").components_include) for n in names
    )
    ids = [i.ext_id for i in snap.incidents]
    assert len(ids) == len(set(ids))


def test_apple_ongoing_event() -> None:
    data = load_json(fixture("apple"))
    svc = next(s for s in data["services"] if s["serviceName"] == "App Store")
    svc["events"][0]["eventStatus"] = "ongoing"
    snap = parse_feed(json.dumps(data).encode(), feed_of("apple"))
    assert snap.indicator == "critical"
    assert next(c for c in snap.components if c.name == "App Store").status == "major_outage"


def test_apple_jsonp_wrapper() -> None:
    wrapped = b"jsonCallback(" + fixture("apple") + b");"
    assert parse_feed(wrapped, feed_of("apple")).components


def test_heroku_stale_scheduled_maintenance_is_not_active() -> None:
    snap = parse_feed(fixture("heroku"), feed_of("heroku"))
    assert snap.indicator == "none"
    maint = [i for i in snap.incidents if i.kind == "maintenance"]
    assert maint and all(not i.active for i in maint)


def test_statusio_operational() -> None:
    snap = parse_feed(fixture("gitlab"), feed_of("gitlab"))
    assert snap.indicator == "none"
    assert snap.components and all(c.status == "operational" for c in snap.components)


def test_rss_empty_channel() -> None:
    snap = parse_feed(fixture("azure"), feed_of("azure"))
    assert snap.indicator == "none"
    assert snap.incidents == []


def test_rss_resolved_items_are_not_active() -> None:
    feed = feed_of("huggingface")
    snap = rss_adapter.parse(fixture("huggingface"), feed)
    assert snap.incidents
    first = snap.incidents[0]
    # Evaluate as if we polled right after the post: it is resolved by its text.
    at = rss_adapter.parse(fixture("huggingface"), feed, now=first.started_at)
    assert not at.incidents[0].active


def test_rss_recent_unresolved_item_is_active() -> None:
    xml = b"""<rss version="2.0"><channel><title>x</title>
      <item><title>Major outage: API unavailable</title><link>https://x/1</link>
      <guid>1</guid><pubDate>Wed, 18 Nov 2026 12:00:00 GMT</pubDate>
      <description>We are investigating.</description></item></channel></rss>"""
    now = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)
    snap = rss_adapter.parse(xml, feed_of("azure"), now=now)
    assert snap.indicator == "critical"
    later = rss_adapter.parse(xml, feed_of("azure"), now=datetime(2026, 11, 19, tzinfo=UTC))
    assert later.indicator == "none"


@pytest.mark.parametrize(
    ("text", "impact"),
    [
        ("Scheduled maintenance for database", "maintenance"),
        ("Major outage affecting all regions", "critical"),
        ("Service unavailable in East US", "major"),
        ("Partial disruption to Pages builds", "major"),
        ("Elevated errors on API", "minor"),
        ("Investigating reports", "minor"),
    ],
)
def test_rss_classifier(text: str, impact: str) -> None:
    assert rss_adapter.classify(text) == impact


def test_rss_rejects_garbage() -> None:
    with pytest.raises(FeedParseError):
        rss_adapter.parse(b"<html><body>nope", feed_of("azure"))


async def test_polite_client_conditional_and_retry() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"ok": True}, headers={"etag": '"v1"'})

    client = PoliteClient("test-ua", min_host_gap_s=0, transport=httpx.MockTransport(handler))
    first = await client.get("https://status.example/x")
    assert first.status == 200 and len(calls) == 2  # retried after 503
    second = await client.get("https://status.example/x")
    assert second.not_modified and second.content == first.content
    assert calls[-1].headers["user-agent"] == "test-ua"
    await client.aclose()
