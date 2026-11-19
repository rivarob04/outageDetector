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
    [
        ("N8W 1H4", "N8W1H4"),
        (" n8w1h4 ", "N8W1H4"),
        ("l3k-5v5", "L3K5V5"),
        ("", ""),
        # several codes: commas, spaces, or both; the space inside one code is kept together
        ("N8W 1H4, N8N 2L9, N9J 1A1", "N8W1H4, N8N2L9, N9J1A1"),
        ("N8W 1H4 N8N 2L9 N9J 1A1", "N8W1H4, N8N2L9, N9J1A1"),
        ("n8w1h4,n8n2l9;n9j1a1", "N8W1H4, N8N2L9, N9J1A1"),
        ("N8W1H4\nN8N 2L9", "N8W1H4, N8N2L9"),
        # three-character areas, and duplicates removed
        ("N8W, N8N, n8w", "N8W, N8N"),
        ("N8W 1H4 N8N", "N8W1H4, N8N"),
    ],
)
def test_normalize_postal_code(value: str, expected: str) -> None:
    assert normalize_postal_code(value) == expected


@pytest.mark.parametrize(
    "value", ["12345", "N8W 1H", "W8W 1H4", "D8W1H4", "N8W 1H44", "N8W1H4, nope", "N8W 1H4 1H4"]
)
def test_normalize_postal_code_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="postal"):
        normalize_postal_code(value)


def test_postal_code_is_runtime_editable_and_validated() -> None:
    assert coerce("postal_code", "n8w 1h4") == "N8W1H4"
    assert coerce("postal_code", "n8w 1h4 n8n 2l9") == "N8W1H4, N8N2L9"
    with pytest.raises(ValueError):
        coerce("postal_code", "nope")


def test_effective_feed_adds_postal_codes() -> None:
    base = feed("teksavvy")
    assert "postal_codes" not in base.options
    tek = feed("teksavvy", "n8w 1h4")
    assert tek.url == base.url and tek.options["postal_codes"] == "N8W1H4"
    assert feed("teksavvy", "N8W1H4, N8N 2L9 N9J").options["postal_codes"] == "N8W1H4,N8N2L9,N9J"
    # an invalid value is ignored rather than breaking the feed
    assert "postal_codes" not in feed("teksavvy", "garbage").options


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


def test_teksavvy_any_of_several_postal_codes_matches() -> None:
    kml = raw("teksavvy/outages.kml")
    # Windsor + Tecumseh + LaSalle: the Port Colborne outage is in none of them
    local = parse_feed(kml, feed("teksavvy", "N8W 1H4, N8N 2L9 N9J 1A1"))
    assert local.incidents == []
    # adding a Port Colborne area picks it up, once
    with_l3k = parse_feed(kml, feed("teksavvy", "N8W 1H4 L3K"))
    assert with_l3k.indicator == "major"
    assert len(with_l3k.active_incidents) == 1


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


# --- areas ------------------------------------------------------------------------


def test_incidents_are_tagged_with_their_areas() -> None:
    kml = raw("teksavvy/outages.kml")
    (anywhere,) = parse_feed(kml, feed("teksavvy")).incidents
    assert anywhere.regions == ["area:L3K"]
    (mine,) = parse_feed(kml, feed("teksavvy", "N8W 1H4, L3K 1A1")).incidents
    assert mine.regions == ["area:L3K"]


def test_incident_id_survives_a_move_to_resolved_issues() -> None:
    kml = raw("teksavvy/outages.kml")
    moved = kml.replace(b"<name>Cable Outage</name>", b"<name>Resolved Issues</name>", 1)
    (open_inc,) = parse_feed(kml, feed("teksavvy")).incidents
    (closed,) = parse_feed(moved, feed("teksavvy")).incidents
    assert open_inc.ext_id == closed.ext_id and not closed.active


def test_cogeco_follows_cable_outages_only_in_your_areas() -> None:
    kml = raw("teksavvy/outages.kml")
    quiet = parse_feed(kml, feed("cogeco"))
    assert quiet.indicator == "none" and quiet.incidents == []
    assert "postal codes" in quiet.description
    assert parse_feed(kml, feed("cogeco", "N8W 1H4")).incidents == []
    near = parse_feed(kml, feed("cogeco", "L3K 5V5"))
    assert near.indicator == "major" and near.active_incidents[0].regions == ["area:L3K"]


def test_isp_area_rows() -> None:
    from pulse.isp_areas import areas_of, build

    assert areas_of(["N8W1H4", "N8W2A2", "N8N", "n9j1a1"]) == ["N8W", "N8N", "N9J"]
    now = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    since = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)

    def inc(
        i: int, sid: str, area: str, start: str, end: str | None, kind: str = "incident"
    ) -> dict:
        return {
            "id": i,
            "service_id": sid,
            "kind": kind,
            "title": f"t{i}",
            "impact": "major",
            "regions": [f"area:{area}"],
            "started_at": datetime.fromisoformat(start),
            "resolved_at": datetime.fromisoformat(end) if end else None,
            "first_seen_at": datetime.fromisoformat(start),
        }

    rows = [
        inc(1, "cogeco", "N8N", "2026-11-18T10:00+00:00", "2026-11-18T12:00+00:00"),
        inc(2, "cogeco", "N8N", "2026-11-18T11:00+00:00", "2026-11-18T13:00+00:00"),  # overlaps 1
        inc(3, "cogeco", "N8N", "2026-11-19T11:30+00:00", None),  # still open
        inc(4, "cogeco", "N8N", "2026-11-01T00:00+00:00", "2026-11-02T00:00+00:00"),  # too old
        inc(5, "cogeco", "N8N", "2026-11-12T11:00+00:00", "2026-11-12T13:00+00:00"),  # clipped
        inc(6, "bell", "K7H", "2026-11-18T00:00+00:00", "2026-11-18T01:00+00:00"),
        inc(7, "bell", "N8N", "2026-11-18T00:00+00:00", None, kind="maintenance"),
    ]
    isps = [("bell", "Bell"), ("cogeco", "Cogeco")]
    n8n, k7h = build(["N8N", "K7H"], isps, rows, since, now)
    assert n8n["area"] == "N8N"
    bell, cogeco = n8n["isps"]
    assert bell == {
        "service_id": "bell",
        "name": "Bell",
        "state": "operational",
        "down_s": 0,
        "outages": [],
    }
    assert cogeco["state"] == "major_outage"
    # 10:00-13:00 merged (3h) + 30 min open + 1h clipped at the window start
    assert cogeco["down_s"] == 3 * 3600 + 1800 + 3600
    assert [o["id"] for o in cogeco["outages"]] == [3, 2, 1, 5]
    assert cogeco["outages"][0]["resolved_at"] is None
    assert k7h["isps"][0]["down_s"] == 3600 and k7h["isps"][1]["outages"] == []
