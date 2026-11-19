"""Per-area ISP outage history: one timeline per forward sortation area (FSA).

Area-aware feeds (Google My Maps trackers) tag each incident with ``area:<FSA>`` regions.
This module turns those incidents into, for every area the user follows, one row per ISP
with its outage segments inside the window, total downtime and the current state. Pure: the
caller passes the rows and the clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

AREA_PREFIX = "area:"


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def areas_of(codes: Iterable[str]) -> list[str]:
    """Distinct FSAs, in the user's order, from normalized postal codes / areas."""
    out: list[str] = []
    for code in codes:
        fsa = code.strip().upper()[:3]
        if len(fsa) == 3 and fsa not in out:
            out.append(fsa)
    return out


def _merge(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build(
    areas: Sequence[str],
    isps: Sequence[tuple[str, str]],
    incidents: Iterable[dict[str, Any]],
    since: datetime,
    now: datetime,
) -> list[dict[str, Any]]:
    """``isps``: (service_id, name) of area-aware ISPs. ``incidents``: store rows."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for inc in incidents:
        if inc.get("kind") != "incident":
            continue
        for region in inc.get("regions") or []:
            if isinstance(region, str) and region.startswith(AREA_PREFIX):
                by_key.setdefault((region[len(AREA_PREFIX) :], inc["service_id"]), []).append(inc)

    out = []
    for fsa in areas:
        rows = []
        for sid, name in isps:
            outages = []
            spans: list[tuple[datetime, datetime]] = []
            active = False
            for inc in by_key.get((fsa, sid), []):
                start = inc.get("started_at") or inc.get("first_seen_at")
                end = inc.get("resolved_at")
                if start is None or (end is not None and end < since) or start > now:
                    continue
                active = active or end is None
                spans.append((max(start, since), min(end or now, now)))
                outages.append(
                    {
                        "id": inc["id"],
                        "title": inc["title"],
                        "impact": inc["impact"],
                        "started_at": iso(start),
                        "resolved_at": iso(end),
                    }
                )
            down_s = sum((e - s).total_seconds() for s, e in _merge(spans))
            outages.sort(key=lambda o: o["started_at"] or "", reverse=True)
            rows.append(
                {
                    "service_id": sid,
                    "name": name,
                    "state": "major_outage" if active else "operational",
                    "down_s": round(down_s),
                    "outages": outages,
                }
            )
        out.append({"area": fsa, "isps": rows})
    return out
