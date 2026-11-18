"""Latency baselines and the simple degraded-latency rule (Phase 3).

Phase 8 adds a time-of-day aware EWMA+MAD detector in ``pulse.signals.anomaly``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

MIN_SAMPLES = 20


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass(frozen=True)
class Baseline:
    n: int
    p50: float
    p95: float


def baseline(samples: Sequence[float]) -> Baseline | None:
    if len(samples) < MIN_SAMPLES:
        return None
    return Baseline(len(samples), percentile(samples, 0.5), percentile(samples, 0.95))


def latency_ratio(current_ms: float, b: Baseline | None) -> float | None:
    if b is None or b.p50 <= 0:
        return None
    return current_ms / b.p50


def is_slow(current_ms: float, b: Baseline | None, factor: float, min_ms: float) -> bool:
    """Slow when above ``factor`` × p95 *and* above an absolute floor (avoids 5ms→20ms noise)."""
    if b is None:
        return False
    return current_ms > b.p95 * factor and current_ms > min_ms


def consecutive_slow(
    recent: Sequence[float], b: Baseline | None, factor: float, min_ms: float, needed: int
) -> bool:
    """True when the last ``needed`` latencies are all slow."""
    tail = list(recent)[-needed:]
    return len(tail) >= needed and all(is_slow(v, b, factor, min_ms) for v in tail)
