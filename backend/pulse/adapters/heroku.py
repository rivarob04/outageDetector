"""Heroku status API v4 (``/api/v4/current-status``)."""

from __future__ import annotations

from typing import Any

from pulse.adapters.base import (
    Component,
    ComponentStatus,
    FeedParseError,
    FeedSnapshot,
    Impact,
    Incident,
    IncidentUpdate,
    load_json,
    max_impact,
    parse_dt,
)
from pulse.catalog.schema import FeedConfig

_COLOR: dict[str, ComponentStatus] = {
    "green": "operational",
    "yellow": "degraded",
    "orange": "partial_outage",
    "red": "major_outage",
    "blue": "maintenance",
}
_COLOR_IMPACT: dict[str, Impact] = {
    "green": "none",
    "yellow": "minor",
    "orange": "major",
    "red": "critical",
    "blue": "maintenance",
}


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    data = load_json(content)
    if not isinstance(data, dict) or "status" not in data:
        raise FeedParseError("not a Heroku status payload")
    components = [
        Component(
            ext_id=str(s.get("system")),
            name=str(s.get("system")),
            status=_COLOR.get(str(s.get("status")), "unknown"),
        )
        for s in data.get("status") or []
    ]
    incidents = [_incident(i, "incident") for i in data.get("incidents") or []]
    incidents += [_incident(i, "maintenance") for i in data.get("scheduled") or []]
    color_impact = max_impact(
        [_COLOR_IMPACT.get(str(s.get("status")), "none") for s in data.get("status") or []]
    )
    inc_impact = max_impact([i.impact for i in incidents if i.active and i.kind == "incident"])
    indicator = max_impact([color_impact, inc_impact])
    return FeedSnapshot(
        indicator=indicator,
        description="All systems operational" if indicator == "none" else "Heroku is degraded",
        components=components,
        incidents=incidents,
    )


def _incident(i: dict[str, Any], kind: str) -> Incident:
    updates_raw = i.get("updates") or []
    updates = [
        IncidentUpdate(
            ext_id=str(u.get("id")),
            status=str(u.get("update_type", "")),
            body=str(u.get("contents") or ""),
            created_at=parse_dt(u.get("created_at")),  # type: ignore[arg-type]
        )
        for u in updates_raw
    ]
    latest = str(updates_raw[0].get("update_type", "")) if updates_raw else ""
    done = bool(i.get("resolved")) or latest in ("resolved", "completed")
    systems = i.get("systems") or []
    impact = (
        max_impact([_COLOR_IMPACT.get(str(s.get("status")), "minor") for s in systems]) or "minor"
    )
    if kind == "maintenance":
        state = str(i.get("state", ""))
        status = "completed" if done else ("in_progress" if state == "in-progress" else "scheduled")
        impact = "maintenance"
    else:
        status = "resolved" if done else "investigating"
        if impact == "none":
            impact = "minor"
    return Incident(
        ext_id=str(i.get("id")),
        title=str(i.get("title") or "Heroku incident"),
        impact=impact,
        status=status,
        kind=kind,  # type: ignore[arg-type]
        url=i.get("full_url"),
        started_at=parse_dt(i.get("created_at")),
        resolved_at=parse_dt(i.get("resolved_at")) if done else None,
        component_ids=[str(s.get("name")) for s in systems],
        updates=updates,
    )
