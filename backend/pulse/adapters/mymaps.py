"""Google My Maps KML export, used by ISPs that publish their outage tracker as a map.

TekSavvy's "Maintenance and Outage Tracker" (embedded on teksavvy.com/network-status) is a
My Maps document; ``/maps/d/kml?mid=<id>&forcekml=1`` returns it as KML. Each folder is a
category ("Cable Outage", "DSL/Bell Fibre Outage", "Resolved Issues", ...) and each placemark
is one outage with the affected postal codes in ``ExtendedData``.

Options:
- ``folders``: regex; only folders whose name matches count (default: all).
- ``resolved_folders``: regex for folders that hold resolved items (default ``resolved``).
- ``postal_code`` (set by Pulse from ``PULSE_POSTAL_CODE``): when present, only placemarks
  listing the same forward sortation area (first three characters) are reported, as
  ``major``; without it every open placemark is reported as ``minor`` (somewhere, not you).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from pulse.adapters.base import (
    FeedParseError,
    FeedSnapshot,
    Impact,
    Incident,
    IncidentUpdate,
    max_impact,
    strip_html,
)
from pulse.catalog.schema import FeedConfig

_NS = "{http://www.opengis.net/kml/2.2}"
_MAINT = re.compile(r"maint|planned", re.I)
_FSA = re.compile(r"\b([A-Z]\d[A-Z])(?:\s?\d[A-Z]\d|\*{3})?\b")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise FeedParseError(f"invalid XML: {exc}") from exc
    if root.tag != f"{_NS}kml":
        raise FeedParseError(f"unexpected root element <{root.tag}>")
    doc = root.find(f"{_NS}Document")
    if doc is None:
        raise FeedParseError("KML without <Document>")

    include = re.compile(feed.options.get("folders", ".*"), re.I)
    resolved_re = re.compile(feed.options.get("resolved_folders", "resolved"), re.I)
    postal = feed.options.get("postal_code", "").upper()
    fsa = postal[:3]

    incidents: list[Incident] = []
    for folder in doc.iter(f"{_NS}Folder"):
        fname = _text(folder, "name")
        resolved = bool(resolved_re.search(fname))
        if not resolved and not include.search(fname):
            continue
        for pm in folder.findall(f"{_NS}Placemark"):
            inc = _incident(pm, fname, resolved, fsa)
            if inc is not None:
                incidents.append(inc)

    active = [i for i in incidents if i.kind == "incident" and i.active]
    return FeedSnapshot(
        indicator=max_impact([i.impact for i in active]),
        description=active[0].title if active else "No reported outages",
        incidents=incidents,
    )


def _text(el: Element, tag: str) -> str:
    return (el.findtext(f"{_NS}{tag}") or "").strip()


def _data(pm: Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in pm.iter(f"{_NS}Data"):
        out[d.get("name", "")] = (d.findtext(f"{_NS}value") or "").strip()
    return out


def _when(value: str) -> datetime | None:
    """My Maps stores dates as epoch microseconds (sometimes ms or s)."""
    if not value.isdigit():
        return None
    n = int(value)
    for div in (1_000_000, 1_000, 1):
        try:
            ts = n / div
            if 946_684_800 <= ts <= 4_102_444_800:  # 2000..2100
                return datetime.fromtimestamp(ts, UTC)
        except (OverflowError, OSError, ValueError):
            continue
    return None


def _incident(pm: Element, folder: str, resolved: bool, fsa: str) -> Incident | None:
    name = _text(pm, "name")
    body = strip_html(_text(pm, "description"))
    data = _data(pm)
    location = data.get("Location", "") or body
    areas = {m.group(1) for m in _FSA.finditer(f"{name} {location}".upper())}
    if fsa and fsa not in areas:
        return None  # an outage somewhere else

    maint = bool(_MAINT.search(folder) or _MAINT.search(name))
    impact: Impact = "maintenance" if maint else ("major" if fsa else "minor")
    if maint:
        status = "completed" if resolved else "in_progress"
    else:
        status = "resolved" if resolved else "investigating"
    reported = _when(data.get("Date Reported", ""))
    when = reported or _EPOCH
    ext = f"{folder}|{name}|{data.get('Date Reported', '')}"
    title = name.title() if name.isupper() else name
    return Incident(
        ext_id=ext,
        title=f"{title} ({folder})" if folder.lower() not in title.lower() else title,
        impact=impact,
        status=status,
        kind="maintenance" if maint else "incident",
        started_at=reported,
        resolved_at=when if resolved else None,
        updates=[IncidentUpdate(f"{ext}#0", status, body or name, when)],
    )
