"""Pure alert-rule logic: which rules fire for a state change, quiet hours, formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pulse.verdict.engine import STATE_RANK

PROBLEM_STATES = ("degraded", "partial_outage", "major_outage")
ALL_SCOPES = ("provider_confirmed", "widespread", "local_only")
SHAPE = {
    "operational": "●",
    "degraded": "▲",
    "partial_outage": "◆",
    "major_outage": "■",
    "maintenance": "◐",
    "unknown": "○",
}
LABEL = {
    "operational": "Operational",
    "degraded": "Degraded",
    "partial_outage": "Partial outage",
    "major_outage": "Major outage",
    "maintenance": "Maintenance",
    "unknown": "Unknown",
}
SCOPE_LABEL = {
    "provider_confirmed": "confirmed by the provider",
    "widespread": "failing from several places",
    "local_only": "only from your network",
    "local_network": "you're offline",
    "none": "",
}


@dataclass
class Rule:
    id: int
    channel_id: int
    services: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    min_state: str = "partial_outage"
    scopes: list[str] = field(default_factory=lambda: ["provider_confirmed", "widespread"])
    quiet_start: str | None = None
    quiet_end: str | None = None
    tz: str = "UTC"
    cooldown_s: int = 600
    recovery: bool = True
    digest: bool = False
    enabled: bool = True

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Rule:
        return cls(**{k: row[k] for k in cls.__dataclass_fields__ if k in row})


def matches_service(rule: Rule, service_id: str, category: str) -> bool:
    if rule.services and service_id not in rule.services:
        return False
    return not (rule.categories and category not in rule.categories)


def is_alertable(rule: Rule, state: str, scope: str) -> bool:
    return (
        state in PROBLEM_STATES
        and STATE_RANK.get(state, 0) >= STATE_RANK.get(rule.min_state, 0)
        and scope in rule.scopes
    )


def decide(rule: Rule, ev: dict[str, Any]) -> str | None:
    """Return "problem", "recovery" or None for a state-change event."""
    if not rule.enabled or not matches_service(rule, ev["service_id"], ev.get("category", "")):
        return None
    now_alert = is_alertable(rule, ev["state"], ev["scope"])
    before = is_alertable(
        rule, ev.get("prev_state") or "operational", ev.get("prev_scope") or "none"
    )
    if now_alert and not before:
        return "problem"
    if now_alert and before and STATE_RANK[ev["state"]] > STATE_RANK[ev["prev_state"]]:
        return "problem"  # escalation, e.g. degraded -> major outage
    if before and ev["state"] == "operational" and rule.recovery:
        return "recovery"
    return None


def in_quiet_hours(rule: Rule, now: datetime) -> bool:
    if not rule.quiet_start or not rule.quiet_end:
        return False
    try:
        tz = ZoneInfo(rule.tz or "UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    local = now.astimezone(tz).time()
    start, end = time.fromisoformat(rule.quiet_start), time.fromisoformat(rule.quiet_end)
    if start <= end:
        return start <= local < end
    return local >= start or local < end  # wraps midnight, e.g. 22:00-07:00


def format_state(ev: dict[str, Any], base_url: str) -> tuple[str, str]:
    state, scope = ev["state"], ev["scope"]
    name = ev.get("service_name") or ev["service_id"]
    if state == "operational":
        title = f"{SHAPE['operational']} {name} recovered"
    else:
        where = SCOPE_LABEL.get(scope, "")
        title = f"{SHAPE.get(state, '')} {name}: {LABEL.get(state, state)}" + (
            f" ({where})" if where else ""
        )
    lines = [f"- {r}" for r in ev.get("reasons", [])[:5]]
    lines += [f"- {a}" for a in ev.get("advisories", [])[:2]]
    if base_url:
        lines.append(f"\n{base_url.rstrip('/')}/service/{ev['service_id']}")
    return title, "\n".join(lines) or LABEL.get(state, state)
