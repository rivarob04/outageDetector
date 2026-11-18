"""Builders for API payloads (kept out of the route functions)."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from pulse.catalog.schema import CATEGORY_LABELS, ServiceDef

UPTIME_TTL_S = 300


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class UptimeCache:
    """90-day daily check-success ratio per service (5-minute cache)."""

    def __init__(self) -> None:
        self._at = 0.0
        self._data: dict[str, dict[str, float]] = {}

    def invalidate(self) -> None:
        self._at = 0.0

    async def get(self, rt: Any) -> dict[str, dict[str, float]]:
        if time.monotonic() - self._at < UPTIME_TTL_S:
            return self._data
        mon = rt.monitor
        probe_to_svc = {
            s.id: sid for sid, specs in mon.specs.items() for s in specs if s.kind != "tls"
        }
        since = datetime.now(UTC) - timedelta(days=90)
        agg: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for r in await rt.store.rollups(list(probe_to_svc), "1d", since):
            sid = probe_to_svc[r["probe_id"]]
            day = r["bucket"].date().isoformat()
            agg[sid][day][0] += r["n_ok"]
            agg[sid][day][1] += r["n"]
        # Today from raw history (rollups lag behind).
        today = datetime.now(UTC).date()
        start = datetime(today.year, today.month, today.day, tzinfo=UTC)
        for pid, sid in probe_to_svc.items():
            h = mon.history.get(pid)
            if not h:
                continue
            cell = [0, 0]
            for ts, ok, *_ in h.items:
                if ts >= start:
                    cell[0] += 1 if ok else 0
                    cell[1] += 1
            if cell[1]:
                agg[sid][today.isoformat()] = cell
        self._data = {
            sid: {d: round(100 * v[0] / v[1], 2) for d, v in days.items() if v[1]}
            for sid, days in agg.items()
        }
        self._at = time.monotonic()
        return self._data


def uptime_series(
    daily: dict[str, float], incident_days: set[str], days: int = 90
) -> list[dict[str, Any]]:
    today = datetime.now(UTC).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        out.append({"day": d, "pct": daily.get(d), "incident": d in incident_days})
    return out


def uptime_pct(series: list[dict[str, Any]]) -> float | None:
    vals = [s["pct"] for s in series if s["pct"] is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def service_base(svc: ServiceDef, source: str, verified: bool | None = None) -> dict[str, Any]:
    return {
        "id": svc.id,
        "name": svc.name,
        "category": svc.category,
        "category_label": CATEGORY_LABELS.get(svc.category, svc.category),
        "icon": svc.icon,
        "homepage": svc.homepage,
        "status_page": svc.status_page,
        "source": source,
        "signal": svc.signal,
        "verified": verified,
        "depends_on": svc.depends_on,
    }


def verdict_dict(v: Any) -> dict[str, Any]:
    if v is None:
        return {
            "state": "unknown",
            "scope": "none",
            "reasons": ["Waiting for the first checks."],
            "advisories": [],
            "since": None,
            "pending_state": None,
        }
    return {
        "state": v.state,
        "scope": v.scope,
        "reasons": list(v.reasons),
        "advisories": list(v.advisories),
        "since": iso(v.since),
        "pending_state": v.pending_state,
    }


def incident_dict(
    inc: dict[str, Any], updates: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    started = inc.get("started_at") or inc.get("first_seen_at")
    resolved = inc.get("resolved_at")
    out = {
        "id": inc["id"],
        "service_id": inc["service_id"],
        "service_name": inc.get("service_name"),
        "ext_id": inc["ext_id"],
        "kind": inc["kind"],
        "title": inc["title"],
        "impact": inc["impact"],
        "status": inc["status"],
        "url": inc["url"],
        "started_at": iso(started),
        "resolved_at": iso(resolved),
        "scheduled_for": iso(inc.get("scheduled_for")),
        "scheduled_until": iso(inc.get("scheduled_until")),
        "first_seen_at": iso(inc.get("first_seen_at")),
        "regions": inc.get("regions") or [],
        "duration_s": (resolved - started).total_seconds() if started and resolved else None,
    }
    if updates is not None:
        out["updates"] = [
            {"status": u["status"], "body": u["body"], "created_at": iso(u["created_at"])}
            for u in updates
        ]
    return out


def mttr(incidents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    names: dict[str, str] = {}
    for inc in incidents:
        if inc["kind"] != "incident" or not inc.get("resolved_at"):
            continue
        start = inc.get("started_at") or inc.get("first_seen_at")
        if start is None:
            continue
        by[inc["service_id"]].append((inc["resolved_at"] - start).total_seconds())
        names[inc["service_id"]] = inc.get("service_name") or inc["service_id"]
    out = {}
    for sid, durs in by.items():
        durs.sort()
        out[sid] = {
            "service_id": sid,
            "service_name": names[sid],
            "count": len(durs),
            "mttr_s": sum(durs) / len(durs),
            "median_s": durs[len(durs) // 2],
        }
    return out
