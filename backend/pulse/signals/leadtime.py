"""'Before they admit it': time between Pulse seeing widespread failures and the provider's post."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

MATCH_BEFORE = timedelta(minutes=30)  # provider posted slightly before we called it widespread
MATCH_AFTER = timedelta(hours=6)


async def lead_times(rt: Any, days: int) -> dict[str, dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    transitions = [
        t
        for t in await rt.store.transitions(since=since, limit=5000)
        if t["scope"] == "widespread"
        and t["from_scope"] != "widespread"
        and t["to_state"] in ("degraded", "partial_outage", "major_outage")
    ]
    if not transitions:
        return {}
    rows, _ = await rt.store.list_incidents(
        kind="incident", limit=5000, tracked_only=False, since=since - MATCH_AFTER
    )
    by_service: dict[str, list[dict[str, Any]]] = {}
    for inc in rows:
        by_service.setdefault(inc["service_id"], []).append(inc)
    out: dict[str, dict[str, Any]] = {}
    for t in transitions:
        sid = t["service_id"]
        svc = rt.monitor.services.get(sid)
        stats = out.setdefault(
            sid,
            {
                "service_name": svc.name if svc else sid,
                "leads": [],
                "confirmed": 0,
                "unconfirmed": 0,
            },
        )
        candidates = [
            i["first_seen_at"]
            for i in by_service.get(sid, [])
            if t["ts"] - MATCH_BEFORE <= i["first_seen_at"] <= t["ts"] + MATCH_AFTER
        ]
        if candidates:
            stats["confirmed"] += 1
            stats["leads"].append((min(candidates) - t["ts"]).total_seconds())
        elif datetime.now(UTC) - t["ts"] > MATCH_AFTER:
            stats["unconfirmed"] += 1
    for stats in out.values():
        leads = stats.pop("leads")
        stats["count"] = stats["confirmed"] + stats["unconfirmed"]
        stats["median_lead_s"] = statistics.median(leads) if leads else None
    return out
