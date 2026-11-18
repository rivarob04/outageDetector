"""Status.io public API (``api.status.io/1.0/status/<page id>``)."""

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
    max_impact,
    parse_dt,
    strip_html,
)
from pulse.catalog.schema import FeedConfig

# 100 Operational, 200 Planned Maintenance, 300 Degraded Performance,
# 400 Partial Service Disruption, 500 Service Disruption, 600 Security Event
_CODE_STATUS: dict[int, ComponentStatus] = {
    100: "operational",
    200: "maintenance",
    300: "degraded",
    400: "partial_outage",
    500: "major_outage",
    600: "major_outage",
}
_CODE_IMPACT: dict[int, Impact] = {
    100: "none",
    200: "maintenance",
    300: "minor",
    400: "major",
    500: "critical",
    600: "critical",
}


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    data = load_json(content)
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or "status_overall" not in result:
        raise FeedParseError("not a status.io payload")
    components: list[Component] = []
    for comp in result.get("status") or []:
        for cont in comp.get("containers") or [
            {"id": "", "name": "", "status_code": comp.get("status_code")}
        ]:
            name = f"{comp.get('name')}" + (f" / {cont.get('name')}" if cont.get("name") else "")
            if not component_allowed(name, feed):
                continue
            components.append(
                Component(
                    ext_id=f"{comp.get('id')}:{cont.get('id')}",
                    name=name,
                    status=_CODE_STATUS.get(int(cont.get("status_code") or 100), "unknown"),
                    group=str(comp.get("name")),
                )
            )
    incidents = [_incident(i, "incident") for i in result.get("incidents") or []]
    maint = result.get("maintenance") or {}
    incidents += [_incident(m, "maintenance", "in_progress") for m in maint.get("active") or []]
    incidents += [_incident(m, "maintenance", "scheduled") for m in maint.get("upcoming") or []]
    overall = result["status_overall"]
    code = int(overall.get("status_code") or 100)
    indicator = max_impact(
        [_CODE_IMPACT.get(code, "minor")]
        + [i.impact for i in incidents if i.active and i.kind == "incident"]
    )
    return FeedSnapshot(
        indicator=indicator,
        description=str(overall.get("status") or ""),
        components=components,
        incidents=incidents,
    )


def _incident(i: dict[str, Any], kind: str, mstatus: str | None = None) -> Incident:
    messages = i.get("messages") or []
    updates = [
        IncidentUpdate(
            ext_id=str(m.get("_id") or f"{i.get('_id')}#{idx}"),
            status=str(m.get("state", "")),
            body=strip_html(str(m.get("details") or "")),
            created_at=parse_dt(m.get("datetime")),  # type: ignore[arg-type]
        )
        for idx, m in enumerate(messages)
    ]
    code = int(i.get("current_status") or i.get("status_code") or 400)
    if kind == "maintenance":
        impact: Impact = "maintenance"
        status = mstatus or "scheduled"
    else:
        impact = _CODE_IMPACT.get(code, "major")
        if impact in ("none", "maintenance"):
            impact = "minor"
        status = "investigating"
    return Incident(
        ext_id=str(i.get("_id") or i.get("id")),
        title=str(i.get("name") or "Incident"),
        impact=impact,
        status=status,
        kind=kind,  # type: ignore[arg-type]
        started_at=parse_dt(i.get("datetime_open") or i.get("datetime_planned_start")),
        scheduled_for=parse_dt(i.get("datetime_planned_start")),
        scheduled_until=parse_dt(i.get("datetime_planned_end")),
        component_ids=[
            str(c.get("_id") or c.get("id")) for c in i.get("components_affected") or []
        ],
        updates=updates,
    )
