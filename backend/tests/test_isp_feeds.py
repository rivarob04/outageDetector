"""Local ISP outage feeds (Cogeco API, Google My Maps trackers) and postal-code resolution.

Fixtures were recorded from the live sites on 2026-11-19 during a real Cogeco fibre cut in
Port Colborne (postal code L3K 5V5); Windsor (N8W 1H4) returned no outage at the same time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulse.adapters import cogeco, mymaps, parse_feed
from pulse.adapters.base import FeedParseError
from pulse.catalog.loader import load_catalog
from pulse.catalog.schema import FeedConfig
from pulse.feeds import effective_feed, normalize_postal_code
from pulse.runtime_settings import coerce

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "feeds"
CATALOG = load_catalog(ROOT / "catalog")
NOW = datetime(2026, 11, 19, 14, 0, tzinfo=UTC)


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def feed(sid: str, postal: str = "") -> FeedConfig:
    f = CATALOG.services[sid].feed
    assert f is not None
    return effective_feed(f, postal)


# --- postal codes ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("N8W 1H4", "N8W1H4"), (" n8w1h4 ", "N8W1H4"), ("l3k-5v5", "L3K5V5"), ("", "")],
)
def test_normalize_postal_code(value: str, expected: str) -> None:
    assert normalize_postal_code(value) == expected


@pytest.mark.parametrize("value", ["12345", "N8W 1H", "W8W 1H4", "D8W1H4", "N8W 1H44"])
def test_normalize_postal_code_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="postal"):
        normalize_postal_code(value)


def test_postal_code_is_runtime_editable_and_validated() -> None:
    assert coerce("postal_code", "n8w 1h4") == "N8W1H4"
    with pytest.raises(ValueError):
        coerce("postal_code", "nope")


def test_effective_feed_uses_postal_url() -> None:
    base = feed("cogeco")
    assert base.url.endswith("/global-outages/residential")
    local = feed("cogeco", "N8W 1H4")
    assert local.url == "https://www.cogeco.ca/en/api/outages/residential/N8W1H4"
    assert local.options["postal_code"] == "N8W1H4"
    # an invalid code is ignored rather than breaking the feed
    assert feed("cogeco", "garbage").url == base.url
    # feeds without a template keep their URL but still learn the postal code
    tek = feed("teksavvy", "N8W1H4")
    assert tek.url == feed("teksavvy").url and tek.options["postal_code"] == "N8W1H4"


# --- Cogeco ---------------------------------------------------------------------


def test_cogeco_global_banner_is_minor() -> None:
    snap = parse_feed(raw("cogeco/global_open.json"), feed("cogeco"))
    assert snap.indicator == "minor"
    (inc,) = snap.active_incidents
    assert inc.ext_id == "82725"
    assert inc.title.startswith("Our customers in Port Colboune, Welland")
    assert "<p>" not in inc.updates[0].body


def test_cogeco_global_resolved_and_empty() -> None:
    resolved = raw("cogeco/global_open.json").replace(b'"resolved":0', b'"resolved":1')
    assert parse_feed(resolved, feed("cogeco")).indicator == "none"
    for body in (b"", b"{}", b"null"):
        assert parse_feed(body, feed("cogeco")).indicator == "none"


def test_cogeco_postal_outage_in_area() -> None:
    snap = cogeco.parse(raw("cogeco/postal_open.json"), feed("cogeco", "L3K 5V5"), NOW)
    assert snap.indicator == "major"
    (inc,) = snap.active_incidents
    assert inc.ext_id == "INC0676153"
    assert inc.title == "Outage near L3K: Internet, TV, Home phone affected"
    assert "fibre cut" in inc.updates[0].body
    assert "Estimated resolution: 2026-11-19T15:47:46" in inc.updates[0].body
    status = {c.name: c.status for c in snap.components}
    assert status == {
        "Internet": "major_outage",
        "TV": "partial_outage",
        "Home phone": "partial_outage",
    }


def test_cogeco_postal_clear() -> None:
    snap = parse_feed(raw("cogeco/postal_none.json"), feed("cogeco", "N8W 1H4"))
    assert snap.indicator == "none"
    assert {c.status for c in snap.components} == {"operational"}


def test_cogeco_postal_resolved_and_planned() -> None:
    closed = raw("cogeco/postal_open.json").replace(
        b'"endTime":null', b'"endTime":"2026-11-19T13:00:00Z"'
    )
    snap = cogeco.parse(closed, feed("cogeco", "L3K5V5"), NOW)
    assert snap.indicator == "none" and not snap.active_incidents
    planned = raw("cogeco/postal_open.json").replace(b'"Unplanned"', b'"Planned"')
    snap = cogeco.parse(planned, feed("cogeco", "L3K5V5"), NOW)
    assert snap.indicator == "none"
    (m,) = snap.active_maintenance
    assert m.impact == "maintenance"


def test_cogeco_skips_channel_only_outages() -> None:
    channel = raw("cogeco/postal_open.json").replace(
        b'"channelList":[""]', b'"channelList":["CTV"]'
    )
    assert cogeco.parse(channel, feed("cogeco", "L3K5V5"), NOW).incidents == []


def test_cogeco_rejects_wrong_shape() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b'"hello"', feed("cogeco"))
    with pytest.raises(FeedParseError):
        parse_feed(b'[{"foo": 1}]', feed("cogeco", "N8W1H4"))


# --- Google My Maps (TekSavvy, Bell via TekSavvy) -----------------------------------


def test_teksavvy_all_areas_without_postal_code() -> None:
    snap = parse_feed(raw("teksavvy/outages.kml"), feed("teksavvy"))
    assert snap.indicator == "minor"
    (inc,) = snap.active_incidents
    assert inc.title == "Cable Outage - Colborne - L3K***"
    assert inc.started_at == datetime(2026, 11, 19, 13, 5, tzinfo=UTC)
    assert "L3K2T4" in inc.updates[0].body


def test_teksavvy_postal_code_filters_by_area() -> None:
    near = parse_feed(raw("teksavvy/outages.kml"), feed("teksavvy", "L3K 1A1"))
    assert near.indicator == "major"
    windsor = parse_feed(raw("teksavvy/outages.kml"), feed("teksavvy", "N8W 1H4"))
    assert windsor.indicator == "none" and windsor.incidents == []


def test_bell_only_follows_the_dsl_fibre_folder() -> None:
    assert parse_feed(raw("teksavvy/outages.kml"), feed("bell")).incidents == []
    bell = raw("teksavvy/outages.kml").replace(
        b"<name>DSL/Bell Fibre Outage</name>\n    </Folder>",
        b"<name>DSL/Bell Fibre Outage</name><Placemark><name>FIBRE OUTAGE - WINDSOR - N8W***</name>"
        b"<description>Location: N8W 1H4</description></Placemark></Folder>",
    )
    snap = parse_feed(bell, feed("bell", "N8W1H4"))
    assert snap.indicator == "major"
    assert snap.active_incidents[0].title.startswith("Fibre Outage - Windsor")


def test_mymaps_resolved_folder_and_bad_xml() -> None:
    moved = raw("teksavvy/outages.kml").replace(
        b"<name>Cable Outage</name>", b"<name>Resolved Issues</name>", 1
    )
    snap = mymaps.parse(moved, feed("teksavvy"))
    assert snap.indicator == "none"
    assert snap.incidents and not snap.incidents[0].active
    with pytest.raises(FeedParseError):
        mymaps.parse(b"<html></html>", feed("teksavvy"))
    with pytest.raises(FeedParseError):
        mymaps.parse(b"not xml", feed("teksavvy"))
