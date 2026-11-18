"""Normalized feed model shared by all adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pulse.catalog.schema import FeedConfig

Impact = Literal["none", "minor", "major", "critical", "maintenance"]
ComponentStatus = Literal[
    "operational", "degraded", "partial_outage", "major_outage", "maintenance", "unknown"
]
IncidentStatus = Literal[
    "investigating", "identified", "monitoring", "resolved", "scheduled", "in_progress", "completed"
]

IMPACT_RANK: dict[str, int] = {"none": 0, "maintenance": 1, "minor": 2, "major": 3, "critical": 4}


class FeedParseError(ValueError):
    """Raised when a payload doesn't match the adapter's expected shape."""


@dataclass
class IncidentUpdate:
    ext_id: str
    status: str
    body: str
    created_at: datetime


@dataclass
class Incident:
    ext_id: str
    title: str
    impact: Impact
    status: str
    kind: Literal["incident", "maintenance"] = "incident"
    url: str | None = None
    started_at: datetime | None = None
    resolved_at: datetime | None = None
    scheduled_for: datetime | None = None
    scheduled_until: datetime | None = None
    component_ids: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    updates: list[IncidentUpdate] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.resolved_at is None and self.status not in (
            "resolved",
            "completed",
            "scheduled",
        )


@dataclass
class Component:
    ext_id: str
    name: str
    status: ComponentStatus
    region: str | None = None
    group: str | None = None


@dataclass
class FeedSnapshot:
    indicator: Impact
    description: str
    components: list[Component] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    fetched_at: datetime | None = None

    @property
    def active_incidents(self) -> list[Incident]:
        return [i for i in self.incidents if i.kind == "incident" and i.active]

    @property
    def active_maintenance(self) -> list[Incident]:
        return [i for i in self.incidents if i.kind == "maintenance" and i.status == "in_progress"]


Parser = Callable[[bytes, FeedConfig], FeedSnapshot]


# --- helpers -------------------------------------------------------------------


def load_json(content: bytes) -> Any:
    """Decode JSON, tolerating BOMs and UTF-16 (AWS Health serves UTF-16)."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = content.decode("utf-16")
    else:
        text = content.decode("utf-8-sig", errors="replace")
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FeedParseError(f"invalid JSON: {exc}") from exc


def parse_dt(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, int | float):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000
        return datetime.fromtimestamp(ts, UTC)
    s = str(value).strip()
    if s.isdigit():
        return parse_dt(int(s))
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def max_impact(impacts: list[Impact]) -> Impact:
    if not impacts:
        return "none"
    return max(impacts, key=lambda i: IMPACT_RANK[i])


def component_allowed(name: str, feed: FeedConfig) -> bool:
    if feed.components_include and not any(
        re.search(p, name, re.I) for p in feed.components_include
    ):
        return False
    return not any(re.search(p, name, re.I) for p in feed.components_exclude)


_IATA = re.compile(r"\(([A-Z]{3})\)\s*$")


def map_region(name: str, feed: FeedConfig, provider_prefix: str | None = None) -> str | None:
    """Resolve a component name to a region id using the feed's region config."""
    if feed.regions is None:
        return None
    if feed.regions == "auto":
        if provider_prefix == "cf":
            m = _IATA.search(name)
            return f"cf:{m.group(1)}" if m else None
        return None
    for pattern, region in feed.regions.items():
        if re.search(pattern, name, re.I):
            return region
    return None


def strip_html(text: str) -> str:
    import html

    if re.search(r"&(lt|#60|#x3c);", text, re.I):  # entity-escaped markup
        text = html.unescape(text)
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()
