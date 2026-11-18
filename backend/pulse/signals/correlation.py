"""Upstream correlation: advise dependents when an upstream provider (or region) is down.

Advisories never change a service's state; they only add context.
"""

from __future__ import annotations

from typing import Any

from pulse.verdict.engine import STATE_RANK

THRESHOLD = STATE_RANK["partial_outage"]
COMPONENT_BAD = {"partial_outage", "major_outage"}
IMPACT_BAD = {"major", "critical"}


class Correlator:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    def advisories(self, service_id: str) -> list[str]:
        mon = self.rt.monitor
        svc = mon.services.get(service_id)
        if svc is None or not svc.depends_on:
            return []
        out: list[str] = []
        for dep in svc.depends_on:
            base, _, region = dep.partition(":")
            if base == service_id or base not in mon.services:
                continue  # upstream must be watched for Pulse to know its state
            name = mon.services[base].name
            if region:
                region_id = dep
                if region_id in mon.feeds.get(base, _Empty).bad_regions:
                    reg = self.rt.catalog.regions.get(region_id)
                    label = reg.name if reg else region_id
                    out.append(f"Possibly affected: {label} is having problems.")
            else:
                v = mon.verdicts.get(base)
                if v and STATE_RANK.get(v.state, 0) >= THRESHOLD and v.scope != "local_only":
                    what = "a major outage" if v.state == "major_outage" else "a partial outage"
                    out.append(f"Possibly affected: {name} has {what}.")
        return out


class _EmptyFeed:
    bad_regions: frozenset[str] = frozenset()


_Empty = _EmptyFeed()
