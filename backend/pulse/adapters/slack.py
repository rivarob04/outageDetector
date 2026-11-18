"""Slack status API v2.0.0 (``/api/v2.0.0/current``)."""

from __future__ import annotations

from typing import Any

from pulse.adapters.base import (
    FeedParseError,
    FeedSnapshot,
    Impact,
    Incident,
    IncidentUpdate,
    load_json,
    max_impact,
    parse_dt,
    strip_html,
)
from pulse.catalog.schema import FeedConfig

# Slack incident types: "incident", "notice", "outage"
_IMPACT: dict[str, Impact] = {"notice": "minor", "incident": "major", "outage": "critical"}


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    data = load_json(content)
    if not isinstance(data, dict) or "status" not in data:
        raise FeedParseError("not a Slack status payload")
    incidents = [_incident(i) for i in data.get("active_incidents") or []]
    active = [i for i in incidents if i.active]
    indicator: Impact = max_impact([i.impact for i in active])
    if data.get("status") != "ok" and indicator == "none":
        indicator = "minor"
    desc = "Slack is up and running" if data.get("status") == "ok" else "Slack is having issues"
    return FeedSnapshot(indicator=indicator, description=desc, incidents=incidents)


def _incident(i: dict[str, Any]) -> Incident:
    kind = str(i.get("type", "incident"))
    status = str(i.get("status", "active"))
    scheduled = status in ("scheduled", "in_progress")
    is_maint = kind == "maintenance" or (scheduled and kind == "notice")
    updates = [
        IncidentUpdate(
            ext_id=f"{i.get('id')}#{idx}",
            status=status,
            body=strip_html(str(n.get("body") or "")),
            created_at=parse_dt(n.get("date_created")),  # type: ignore[arg-type]
        )
        for idx, n in enumerate(i.get("notes") or [])
    ]
    resolved = status in ("resolved", "completed")
    return Incident(
        ext_id=str(i.get("id")),
        title=str(i.get("title") or "Slack incident"),
        impact="maintenance" if is_maint else _IMPACT.get(kind, "major"),
        status="resolved" if resolved else ("in_progress" if is_maint else "investigating"),
        kind="maintenance" if is_maint else "incident",
        url=i.get("url"),
        started_at=parse_dt(i.get("date_created")),
        resolved_at=parse_dt(i.get("date_updated")) if resolved else None,
        component_ids=[str(s) for s in i.get("services") or []],
        updates=updates,
    )
