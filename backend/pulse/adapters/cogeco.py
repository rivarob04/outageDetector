"""Cogeco (Ontario/Quebec cable ISP) outage API, as used by cogeco.ca/en/support/outages.

Two shapes, both recorded from the live site:

- ``/en/api/on/global-outages/residential``: one object, the site-wide outage banner for the
  province (or an empty body when there is none). Used when no postal code is configured.
- ``/en/api/outages/residential/<POSTALCODE>``: a list of outages affecting that postal code
  (``[]`` when clear), with per-service flags: ``hsiImpacted`` (internet), ``videoImpacted``
  (TV) and ``voipImpacted`` (home phone).
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    strip_html,
)
from pulse.catalog.schema import FeedConfig

PAGE = "https://www.cogeco.ca/en/support/outages/"
_SERVICES = (
    ("hsiImpacted", "internet", "Internet"),
    ("videoImpacted", "tv", "TV"),
    ("voipImpacted", "phone", "Home phone"),
)
_CLOSED = {"closed", "resolved", "completed", "cancelled", "canceled"}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def parse(content: bytes, feed: FeedConfig, now: datetime | None = None) -> FeedSnapshot:
    now = now or datetime.now(UTC)
    postal_code = feed.options.get("postal_code", "")
    data = load_json(content)
    if data is None or data == {} or data == []:
        snap = FeedSnapshot(indicator="none", description="No reported outages")
        if postal_code:
            snap.components = _components({})
        return snap
    if isinstance(data, dict):
        return _global(data)
    if isinstance(data, list):
        return _postal(data, postal_code, now)
    raise FeedParseError(f"unexpected Cogeco payload: {type(data).__name__}")


def _components(worst: dict[str, ComponentStatus]) -> list[Component]:
    return [Component(f"cogeco:{k}", name, worst.get(k, "operational")) for _, k, name in _SERVICES]


def _title(text: str, fallback: str) -> str:
    first = text.replace("\n", " ").strip().split(". ")[0].strip().rstrip(".")
    if not first:
        return fallback
    return first if len(first) <= 140 else first[:137].rstrip() + "…"


def _global(o: dict[str, Any]) -> FeedSnapshot:
    if "ticketId" not in o or "status" not in o:
        raise FeedParseError("Cogeco global outage without ticketId/status")
    status = str(o.get("status") or "").lower()
    closed = status in _CLOSED or bool(o.get("resolved"))
    body = strip_html(str(o.get("description") or ""))
    ext = str(o["ticketId"])
    started = parse_dt(o.get("reportedTime"))
    updated = parse_dt(o.get("lastUpdatedTime")) or started or _EPOCH
    run_status = "resolved" if closed else "investigating"
    inc = Incident(
        ext_id=ext,
        title=_title(body, "Cogeco outage"),
        # The provincial banner names *some* area, not necessarily yours: keep it minor.
        impact="minor",
        status=run_status,
        url=PAGE,
        started_at=started,
        resolved_at=updated if closed else None,
        updates=[IncidentUpdate(f"{ext}#{updated.isoformat()}", run_status, body, updated)],
    )
    return FeedSnapshot(
        indicator="none" if closed else "minor",
        description="No reported outages" if closed else inc.title,
        incidents=[inc],
    )


def _postal(items: list[Any], postal_code: str, now: datetime) -> FeedSnapshot:
    incidents: list[Incident] = []
    worst: dict[str, ComponentStatus] = {}
    area = f" near {postal_code[:3]}" if postal_code else ""
    for o in items:
        if not isinstance(o, dict) or "ticketId" not in o:
            raise FeedParseError("Cogeco outage entry without ticketId")
        if any(c for c in o.get("channelList") or []):
            continue  # single-channel TV outages; cogeco.ca hides these as well
        planned = str(o.get("outageType") or "").lower() == "planned"
        status = str(o.get("status") or "").lower()
        ended = parse_dt(o.get("endTime"))
        closed = status in _CLOSED or ended is not None
        start = parse_dt(o.get("startTime"))
        eta = parse_dt(o.get("estimatedResolution"))
        hit = [(k, name) for flag, k, name in _SERVICES if o.get(flag)]

        impact: Impact
        if planned:
            impact = "maintenance"
            if closed:
                run_status = "completed"
            elif start is not None and start > now:
                run_status = "scheduled"
            else:
                run_status = "in_progress"
        else:
            impact = "major" if any(k == "internet" for k, _ in hit) else "minor"
            run_status = "resolved" if closed else "investigating"

        body = strip_html(str(o.get("description") or ""))
        if eta and not closed:
            body += f"\nEstimated resolution: {eta.isoformat()}"
        ext = str(o["ticketId"])
        updated = parse_dt(o.get("lastUpdatedTime")) or start or _EPOCH
        names = ", ".join(name for _, name in hit) or "Services"
        inc = Incident(
            ext_id=ext,
            title=f"{'Maintenance' if planned else 'Outage'}{area}: {names} affected",
            impact=impact,
            status=run_status,
            kind="maintenance" if planned else "incident",
            url=PAGE,
            started_at=start,
            resolved_at=(ended or updated) if closed else None,
            scheduled_for=start if planned else None,
            scheduled_until=eta if planned else None,
            component_ids=[f"cogeco:{k}" for k, _ in hit],
            updates=[IncidentUpdate(f"{ext}#{updated.isoformat()}", run_status, body, updated)],
        )
        incidents.append(inc)
        if inc.kind == "incident" and inc.active:
            for k, _ in hit:
                worst[k] = "major_outage" if k == "internet" else "partial_outage"
        elif inc.kind == "maintenance" and run_status == "in_progress":
            for k, _ in hit:
                worst.setdefault(k, "maintenance")

    active = [i for i in incidents if i.kind == "incident" and i.active]
    return FeedSnapshot(
        indicator=max_impact([i.impact for i in active]),
        description=active[0].title if active else "No reported outages",
        components=_components(worst),
        incidents=incidents,
    )
