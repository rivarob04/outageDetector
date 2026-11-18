"""Apple System Status (``system_status_en_US.js``; JSON, sometimes JSONP-wrapped)."""

from __future__ import annotations

import re
from typing import Any

from pulse.adapters.base import (
    Component,
    FeedParseError,
    FeedSnapshot,
    Impact,
    Incident,
    IncidentUpdate,
    component_allowed,
    load_json,
    max_impact,
    parse_dt,
)
from pulse.catalog.schema import FeedConfig

_IMPACT: dict[str, Impact] = {
    "Outage": "critical",
    "Issue": "major",
    "Performance": "minor",
    "Maintenance": "maintenance",
}
_JSONP = re.compile(rb"^\s*[\w.$]+\((.*)\)\s*;?\s*$", re.S)


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    m = _JSONP.match(content)
    data = load_json(m.group(1) if m else content)
    if not isinstance(data, dict) or "services" not in data:
        raise FeedParseError("not an Apple system status payload")
    components: list[Component] = []
    incidents: list[Incident] = []
    for svc in data["services"]:
        name = str(svc.get("serviceName", ""))
        if not component_allowed(name, feed):
            continue
        active_events = [e for e in svc.get("events") or [] if _is_active(e)]
        status = "operational"
        if active_events:
            worst = max_impact(
                [_IMPACT.get(str(e.get("statusType")), "minor") for e in active_events]
            )
            status = {
                "critical": "major_outage",
                "major": "partial_outage",
                "maintenance": "maintenance",
            }.get(worst, "degraded")
        components.append(Component(ext_id=name, name=name, status=status))  # type: ignore[arg-type]
        incidents.extend(_incident(name, e) for e in svc.get("events") or [])
    # The same event is listed under each affected service: de-duplicate.
    unique: dict[str, Incident] = {}
    for inc in incidents:
        if inc.ext_id in unique:
            unique[inc.ext_id].component_ids += inc.component_ids
        else:
            unique[inc.ext_id] = inc
    incs = list(unique.values())
    active = [i for i in incs if i.active and i.kind == "incident"]
    indicator = max_impact([i.impact for i in active])
    if indicator == "none" and any(i.kind == "maintenance" and i.active for i in incs):
        indicator = "maintenance"
    return FeedSnapshot(
        indicator=indicator,
        description="All systems available" if indicator == "none" else "Some services affected",
        components=components,
        incidents=incs,
    )


def _is_active(e: dict[str, Any]) -> bool:
    return str(e.get("eventStatus", "")).lower() in ("ongoing", "active", "in progress")


def _incident(service: str, e: dict[str, Any]) -> Incident:
    stype = str(e.get("statusType", "Issue"))
    is_maint = stype == "Maintenance"
    ev_status = str(e.get("eventStatus", "")).lower()
    resolved = ev_status in ("resolved", "completed")
    start = parse_dt(e.get("epochStartDate"))
    end = parse_dt(e.get("epochEndDate"))
    if is_maint:
        status = "completed" if resolved else ("in_progress" if _is_active(e) else "scheduled")
    else:
        status = "resolved" if resolved else "investigating"
    msg_id = str(e.get("messageId") or f"{service}-{e.get('epochStartDate')}")
    return Incident(
        ext_id=msg_id,
        title=f"{service}: {e.get('usersAffected') or stype}",
        impact="maintenance" if is_maint else _IMPACT.get(stype, "major"),
        status=status,
        kind="maintenance" if is_maint else "incident",
        url="https://www.apple.com/support/systemstatus/",
        started_at=start,
        resolved_at=end if resolved else None,
        scheduled_for=start if is_maint else None,
        scheduled_until=end if is_maint else None,
        component_ids=[service],
        updates=[
            IncidentUpdate(
                ext_id=f"{msg_id}#0",
                status=ev_status,
                body=str(e.get("message") or ""),
                created_at=start or parse_dt(0),  # type: ignore[arg-type]
            )
        ],
    )
