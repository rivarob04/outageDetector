"""Generic RSS 2.0 / Atom adapter with heuristic severity parsing.

Feeds list *posts*, not state; an item counts as active when it is recent
(``options.active_hours``, default 6h) and doesn't read as resolved.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from pulse.adapters.base import (
    FeedParseError,
    FeedSnapshot,
    Impact,
    Incident,
    IncidentUpdate,
    max_impact,
    parse_dt,
    strip_html,
)
from pulse.catalog.schema import FeedConfig

_ATOM = "{http://www.w3.org/2005/Atom}"
_RESOLVED = re.compile(
    r"\b(resolved|mitigated|recovered|completed|restored|post[- ]incident)\b", re.I
)
_MAINT = re.compile(r"\b(maintenance|scheduled)\b", re.I)
_CRITICAL = re.compile(r"\b(outage|unavailable|down|major)\b", re.I)
_MAJOR = re.compile(r"\b(disruption|errors?|failures?|degraded|partial)\b", re.I)


def classify(text: str) -> Impact:
    if _MAINT.search(text):
        return "maintenance"
    if _CRITICAL.search(text):
        return "critical" if re.search(r"\b(major|complete|full)\b", text, re.I) else "major"
    if _MAJOR.search(text):
        return "major" if re.search(r"\bpartial|disruption\b", text, re.I) else "minor"
    return "minor"


def parse(content: bytes, feed: FeedConfig, now: datetime | None = None) -> FeedSnapshot:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise FeedParseError(f"invalid XML: {exc}") from exc
    now = now or datetime.now(UTC)
    window = timedelta(hours=float(feed.options.get("active_hours", "6")))
    items = _items(root)
    if root.tag not in ("rss", f"{_ATOM}feed") and not items:
        raise FeedParseError(f"unexpected root element <{root.tag}>")
    incidents: list[Incident] = []
    for title, link, body, guid, published in items[:50]:
        text = f"{title}\n{body}"
        impact = classify(text)
        resolved = bool(_RESOLVED.search(title) or _RESOLVED.search(body[:400]))
        recent = published is not None and now - published <= window
        is_maint = impact == "maintenance"
        if is_maint:
            status = "completed" if resolved or not recent else "in_progress"
        else:
            status = "resolved" if resolved or not recent else "investigating"
        incidents.append(
            Incident(
                ext_id=guid or link or title,
                title=title,
                impact=impact,
                status=status,
                kind="maintenance" if is_maint else "incident",
                url=link or None,
                started_at=published,
                resolved_at=published if status in ("resolved", "completed") else None,
                updates=[
                    IncidentUpdate(
                        ext_id=f"{guid or link}#0",
                        status=status,
                        body=body,
                        created_at=published or now,
                    )
                ],
            )
        )
    active = [i for i in incidents if i.active and i.kind == "incident"]
    indicator = max_impact([i.impact for i in active])
    if indicator == "none" and any(i.kind == "maintenance" and i.active for i in incidents):
        indicator = "maintenance"
    return FeedSnapshot(
        indicator=indicator,
        description="No recent incidents"
        if indicator == "none"
        else f"{len(active)} recent incident(s)",
        incidents=incidents,
    )


def _text(el: Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _items(root: Element) -> list[tuple[str, str, str, str, datetime | None]]:
    out: list[tuple[str, str, str, str, datetime | None]] = []
    for it in root.iter("item"):
        out.append(
            (
                _text(it.find("title")),
                _text(it.find("link")),
                strip_html(_text(it.find("description"))),
                _text(it.find("guid")),
                parse_dt(_text(it.find("pubDate")) or _text(it.find(f"{_ATOM}updated"))),
            )
        )
    for en in root.iter(f"{_ATOM}entry"):
        link_el = en.find(f"{_ATOM}link")
        out.append(
            (
                _text(en.find(f"{_ATOM}title")),
                link_el.get("href", "") if link_el is not None else "",
                strip_html(_text(en.find(f"{_ATOM}content")) or _text(en.find(f"{_ATOM}summary"))),
                _text(en.find(f"{_ATOM}id")),
                parse_dt(_text(en.find(f"{_ATOM}updated")) or _text(en.find(f"{_ATOM}published"))),
            )
        )
    return out
