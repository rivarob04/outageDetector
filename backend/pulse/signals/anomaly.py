"""Time-of-day aware latency anomaly detection (EWMA vs. median/MAD per hour bucket)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MIN_BUCKET = 12
MIN_FALLBACK = 20
Z_THRESHOLD = 4.0
ALPHA = 0.4
EWMA_WINDOW = 6


@dataclass(frozen=True)
class Robust:
    median: float
    mad: float
    n: int


def robust(values: Sequence[float]) -> Robust | None:
    if not values:
        return None
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    return Robust(med, mad, len(values))


def ewma(values: Sequence[float], alpha: float = ALPHA) -> float:
    acc = values[0]
    for v in values[1:]:
        acc = alpha * v + (1 - alpha) * acc
    return acc


def judge(current: float, base: Robust, factor: float, min_ms: float) -> tuple[bool, float]:
    """Return (slow?, ratio to median). MAD is floored so very stable services aren't twitchy."""
    mad = max(base.mad, base.median * 0.05, 1.0)
    z = (current - base.median) / (1.4826 * mad)
    ratio = current / base.median if base.median > 0 else 0.0
    slow = z >= Z_THRESHOLD and current >= factor * base.median and current - base.median >= min_ms
    return slow, ratio


class AnomalyDetector:
    """Hourly baselines from 7 days of 5-minute rollups; refreshed by a scheduler job."""

    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.hourly: dict[str, dict[int, Robust]] = {}
        self.refreshed_at: datetime | None = None

    async def refresh(self) -> None:
        mon = self.rt.monitor
        probe_ids = [s.id for specs in mon.specs.values() for s in specs if s.kind != "tls"]
        since = datetime.now(UTC) - timedelta(days=7)
        buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        for r in await self.rt.store.rollups(probe_ids, "5m", since):
            if r["p50_ms"] is not None:
                buckets[r["probe_id"]][r["bucket"].hour].append(r["p50_ms"])
        self.hourly = {
            pid: {h: b for h, vals in hours.items() if (b := robust(vals)) and b.n >= MIN_BUCKET}
            for pid, hours in buckets.items()
        }
        self.refreshed_at = datetime.now(UTC)

    def __call__(
        self, probe_id: str, series: list[tuple[datetime, float]], current: float
    ) -> tuple[bool, float | None, str | None]:
        if not series:
            return False, None, None
        s = self.rt.settings
        recent = [v for _t, v in series[-EWMA_WINDOW:]]
        level = ewma(recent)
        # All of the recent window must be elevated: one slow request isn't an incident.
        now = series[-1][0]
        hour_base = self.hourly.get(probe_id, {}).get(now.hour)
        if hour_base is not None:
            base, basis = hour_base, "for this hour"
        else:
            cutoff = now - timedelta(hours=24)
            history = [v for t, v in series[:-EWMA_WINDOW] if t >= cutoff]
            if len(history) < MIN_FALLBACK:
                return False, None, None
            base_opt = robust(history)
            if base_opt is None:
                return False, None, None
            base, basis = base_opt, None
        slow_now, ratio = judge(level, base, s.latency_factor, s.latency_min_ms)
        sustained = sum(
            1
            for v in recent[-s.fail_threshold :]
            if judge(v, base, s.latency_factor, s.latency_min_ms)[0]
        )
        slow = slow_now and sustained >= min(s.fail_threshold, len(recent))
        return slow, (round(ratio, 1) if slow else None), (basis if slow else None)
