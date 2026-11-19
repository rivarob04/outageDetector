"""Local ISP outage feeds (Google My Maps trackers) and postal-code resolution.

The TekSavvy fixture was recorded from the live tracker on 2026-11-19 during a real cable
outage in Port Colborne (L3K).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulse.adapters import mymaps, parse_feed
from pulse.adapters.base import FeedParseError
from pulse.catalog.loader import load_catalog
from pulse.catalog.schema import FeedConfig
from pulse.feeds import effective_feed, normalize_postal_code
from pulse.runtime_settings import coerce

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "feeds"
CATALOG = load_catalog(ROOT / "catalog")


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


def test_effective_feed_adds_postal_code() -> None:
    base = feed("teksavvy")
    assert "postal_code" not in base.options
    tek = feed("teksavvy", "n8w 1h4")
    assert tek.url == base.url and tek.options["postal_code"] == "N8W1H4"
    # an invalid code is ignored rather than breaking the feed
    assert "postal_code" not in feed("teksavvy", "garbage").options


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
