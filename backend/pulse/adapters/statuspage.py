"""Atlassian Statuspage ``/api/v2/summary.json`` adapter.

Also used for pages that expose a Statuspage-compatible API.
"""

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
    component_allowed,
    load_json,
    map_region,
    parse_dt,
    strip_html,
)
from pulse.catalog.schema import FeedConfig

_COMPONENT_STATUS: dict[str, ComponentStatus] = {
    "operational": "operational",
    "degraded_performance": "degraded",
    "partial_outage": "partial_outage",
    "major_outage": "major_outage",
    "under_maintenance": "maintenance",
}
_IMPACT: dict[str, Impact] = {
    "none": "none",
    "minor": "minor",
    "major": "major",
    "critical": "critical",
    "maintenance": "maintenance",
}


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    data = load_json(content)
    if not isinstance(data, dict) or "status" not in data:
        raise FeedParseError("not a Statuspage summary (missing 'status')")
    prefix = "cf" if "cloudflarestatus" in feed.url else None
    groups = {
        c["id"]: c.get("name", "") for c in data.get("components", []) if c.get("group") is True
    }
    components: list[Component] = []
    for c in data.get("components", []):
        if c.get("group") is True:
            continue
        name = str(c.get("name", ""))
        group_name = groups.get(c.get("group_id") or "")
        full = f"{group_name} / {name}" if group_name else name
        if not component_allowed(full, feed):
            continue
        components.append(
            Component(
                ext_id=str(c["id"]),
                name=name,
                status=_COMPONENT_STATUS.get(str(c.get("status")), "unknown"),
                region=map_region(name, feed, prefix),
                group=group_name,
            )
        )
    allowed = {c.ext_id for c in components}
    filtered = bool(feed.components_include or feed.components_exclude)

    incidents: list[Incident] = []
    for raw in data.get("incidents", []):
        inc = _incident(raw, "incident", feed, prefix)
        if filtered and inc.component_ids and not set(inc.component_ids) & allowed:
            continue
        incidents.append(inc)
    for raw in data.get("scheduled_maintenances", []):
        inc = _incident(raw, "maintenance", feed, prefix)
        if filtered and inc.component_ids and not set(inc.component_ids) & allowed:
            continue
        incidents.append(inc)

    status = data.get("status") or {}
    indicator = _IMPACT.get(str(status.get("indicator", "none")), "none")
    if filtered:
        # The page-wide indicator covers components we filtered out; derive it.
        from pulse.adapters.base import max_impact

        indicator = max_impact([i.impact for i in incidents if i.active and i.kind == "incident"])
        if indicator == "none" and any(
            i.kind == "maintenance" and i.status == "in_progress" for i in incidents
        ):
            indicator = "maintenance"
    return FeedSnapshot(
        indicator=indicator,
        description=str(status.get("description") or ""),
        components=components,
        incidents=incidents,
    )


def _incident(raw: dict[str, Any], kind: str, feed: FeedConfig, prefix: str | None) -> Incident:
    comps = raw.get("components") or []
    updates = [
        IncidentUpdate(
            ext_id=str(u.get("id")),
            status=str(u.get("status", "")),
            body=strip_html(str(u.get("body") or "")),
            created_at=parse_dt(u.get("created_at") or u.get("display_at")) or parse_dt(0),  # type: ignore[arg-type]
        )
        for u in raw.get("incident_updates") or []
    ]
    impact = _IMPACT.get(str(raw.get("impact", "none")), "none")
    if kind == "maintenance":
        impact = "maintenance"
    regions = [r for r in (map_region(str(c.get("name", "")), feed, prefix) for c in comps) if r]
    return Incident(
        ext_id=str(raw["id"]),
        title=str(raw.get("name", "Incident")),
        impact=impact,
        status=str(raw.get("status", "investigating")),
        kind=kind,  # type: ignore[arg-type]
        url=raw.get("shortlink") or None,
        started_at=parse_dt(raw.get("started_at") or raw.get("created_at")),
        resolved_at=parse_dt(raw.get("resolved_at")),
        scheduled_for=parse_dt(raw.get("scheduled_for")),
        scheduled_until=parse_dt(raw.get("scheduled_until")),
        component_ids=[str(c.get("id")) for c in comps if c.get("id")],
        regions=sorted(set(regions)),
        updates=updates,
    )
