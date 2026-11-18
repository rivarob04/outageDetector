"""Google Cloud and Google Workspace status dashboards (``incidents.json``).

Both dashboards share the same incident schema.
"""

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
)
from pulse.catalog.schema import FeedConfig

_IMPACT: dict[str, Impact] = {
    "SERVICE_INFORMATION": "minor",
    "SERVICE_DISRUPTION": "major",
    "SERVICE_OUTAGE": "critical",
}
MAX_HISTORY = 50


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    return _parse(content, feed, region_prefix="gcp")


def parse_workspace(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    return _parse(content, feed, region_prefix=None)


def _parse(content: bytes, feed: FeedConfig, region_prefix: str | None) -> FeedSnapshot:
    data = load_json(content)
    if not isinstance(data, list):
        raise FeedParseError("incidents.json must be a JSON list")
    items = [d for d in data if isinstance(d, dict) and "id" in d]
    # The feed is newest-first and long; keep active ones + recent history.
    active_raw = [d for d in items if not d.get("end")]
    recent = [d for d in items if d.get("end")][:MAX_HISTORY]
    incidents = [_incident(d, region_prefix) for d in active_raw + recent]
    active = [i for i in incidents if i.active]
    indicator = max_impact([i.impact for i in active])
    desc = "All services available" if not active else f"{len(active)} active incident(s)"
    return FeedSnapshot(indicator=indicator, description=desc, incidents=incidents)


def _incident(d: dict[str, Any], region_prefix: str | None) -> Incident:
    updates = [
        IncidentUpdate(
            ext_id=f"{d['id']}#{u.get('when') or u.get('created')}",
            status=str(u.get("status", "")).lower(),
            body=str(u.get("text") or ""),
            created_at=parse_dt(u.get("when") or u.get("created")),  # type: ignore[arg-type]
        )
        for u in d.get("updates") or []
    ]
    locations = d.get("currently_affected_locations") or []
    if d.get("end"):
        locations = d.get("previously_affected_locations") or locations
    regions = (
        [f"{region_prefix}:{loc['id']}" for loc in locations if loc.get("id") != "global"]
        if region_prefix
        else []
    )
    uri = str(d.get("uri") or "")
    base = (
        "https://status.cloud.google.com/"
        if region_prefix
        else "https://www.google.com/appsstatus/dashboard/"
    )
    resolved = parse_dt(d.get("end"))
    return Incident(
        ext_id=str(d["id"]),
        title=str(d.get("external_desc") or d.get("service_name") or "Incident").strip(),
        impact=_IMPACT.get(str(d.get("status_impact")), "minor"),
        status="resolved" if resolved else "investigating",
        url=(base + uri.lstrip("/")) if uri else None,
        started_at=parse_dt(d.get("begin") or d.get("created")),
        resolved_at=resolved,
        component_ids=[str(p.get("id")) for p in d.get("affected_products") or []],
        regions=regions,
        updates=updates,
    )
