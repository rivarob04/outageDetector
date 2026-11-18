"""Resolve tracked services (catalog + custom) into definitions and probe specs."""

from __future__ import annotations

import re
from typing import Any

from pulse.catalog.loader import Catalog
from pulse.catalog.schema import ProbeConfig, ServiceDef
from pulse.config import Settings
from pulse.probes.types import ProbeSpec


def service_def(row: dict[str, Any], catalog: Catalog) -> ServiceDef | None:
    if row["source"] == "catalog":
        return catalog.get(row["id"])
    if row.get("config"):
        return ServiceDef.model_validate(row["config"])
    return None


def probe_specs(svc: ServiceDef, settings: Settings, custom: bool) -> list[ProbeSpec]:
    specs: list[ProbeSpec] = []
    counters: dict[str, int] = {}
    for p in svc.probes:
        idx = counters.get(p.kind, 0)
        counters[p.kind] = idx + 1
        specs.append(_spec(svc.id, p, idx, settings, custom))
    return specs


def _spec(service_id: str, p: ProbeConfig, idx: int, settings: Settings, custom: bool) -> ProbeSpec:
    return ProbeSpec(
        id=f"{service_id}:{p.kind}:{idx}",
        service_id=service_id,
        kind=p.kind,
        url=p.url,
        host=p.host,
        port=p.port,
        method=p.method,
        accept_status=tuple(p.accept_status),
        keyword=p.keyword,
        headers=tuple(sorted(p.headers.items())),
        timeout_s=p.timeout_s or settings.http_timeout_s,
        interval_s=max(15, p.interval_s or settings.probe_interval_s),
        guarded=custom,
    )


_SLUG_BAD = re.compile(r"[^a-z0-9-]+")


def slugify(name: str) -> str:
    s = _SLUG_BAD.sub("-", name.lower()).strip("-")
    return (s or "service")[:50]
