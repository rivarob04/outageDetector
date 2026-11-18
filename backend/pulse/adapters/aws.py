"""AWS Health public dashboard (``/public/currentevents``, UTF-16 JSON list)."""

from __future__ import annotations

import re
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

# AWS status codes: 0 resolved/ok, 1 informational, 2 degradation, 3 disruption.
_IMPACT: dict[str, Impact] = {"0": "none", "1": "minor", "2": "major", "3": "critical"}
_ARN_REGION = re.compile(r"^arn:aws:health:([a-z0-9-]*):")


def parse(content: bytes, feed: FeedConfig) -> FeedSnapshot:
    data = load_json(content)
    if data is None:
        data = []
    if not isinstance(data, list):
        raise FeedParseError("AWS currentevents must be a JSON list")
    incidents = [_incident(e) for e in data if isinstance(e, dict)]
    active = [i for i in incidents if i.active]
    indicator = max_impact([i.impact for i in active])
    desc = (
        "All AWS services operating normally"
        if not active
        else f"{len(active)} active AWS event(s)"
    )
    return FeedSnapshot(indicator=indicator, description=desc, incidents=incidents)


def _incident(e: dict[str, Any]) -> Incident:
    arn = str(e.get("arn", ""))
    m = _ARN_REGION.match(arn)
    region = m.group(1) if m else ""
    log = e.get("event_log") or []
    updates = [
        IncidentUpdate(
            ext_id=f"{arn}#{idx}",
            status=_IMPACT.get(str(item.get("status")), "minor"),
            body=str(item.get("message") or ""),
            created_at=parse_dt(item.get("timestamp")) or parse_dt(e.get("date")),  # type: ignore[arg-type]
        )
        for idx, item in enumerate(log)
    ]
    status_code = str(e.get("status", "1"))
    impact = _IMPACT.get(status_code, "minor")
    resolved = status_code == "0"
    title_bits = [str(e.get("service_name") or "AWS"), str(e.get("summary") or "")]
    if e.get("region_name"):
        title_bits.append(f"({e['region_name']})")
    return Incident(
        ext_id=arn or str(e.get("date")),
        title=" ".join(b for b in title_bits if b).strip(),
        impact=impact if not resolved else "none",
        status="resolved" if resolved else "investigating",
        url="https://health.aws.amazon.com/health/status",
        started_at=parse_dt(e.get("date")),
        resolved_at=updates[-1].created_at if resolved and updates else None,
        component_ids=[str(k) for k in (e.get("impacted_services") or {})],
        regions=[f"aws:{region}"] if region else [],
        updates=list(reversed(updates)),
    )
