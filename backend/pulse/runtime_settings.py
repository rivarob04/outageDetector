"""Settings that can be changed at runtime (UI or config file), on top of env vars."""

from __future__ import annotations

from typing import Any

from pulse.config import Settings

# key -> (type, min, max)
EDITABLE: dict[str, tuple[type, float | None, float | None]] = {
    "public_view": (bool, None, None),
    "allow_private_targets": (bool, None, None),
    "canary_targets": (str, None, None),
    "dns_resolvers": (str, None, None),
    "feed_interval_s": (int, 30, 3600),
    "probe_interval_s": (int, 15, 3600),
    "fail_threshold": (int, 1, 10),
    "recover_threshold": (int, 1, 20),
    "latency_factor": (float, 1.5, 20),
    "latency_min_ms": (float, 0, 10000),
    "retention_raw_days": (int, 1, 90),
    "retention_5m_days": (int, 7, 730),
    "retention_incident_days": (int, 30, 3650),
}


def coerce(key: str, value: Any) -> Any:
    typ, lo, hi = EDITABLE[key]
    if typ is bool:
        if isinstance(value, str):
            value = value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if typ is str:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        return value.strip()
    v = typ(value)
    if lo is not None and v < lo:
        raise ValueError(f"{key} must be >= {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"{key} must be <= {hi}")
    return v


def apply_overrides(settings: Settings, overrides: dict[str, Any]) -> None:
    coerced = {k: coerce(k, v) for k, v in overrides.items() if k in EDITABLE}
    for k, v in coerced.items():
        setattr(settings, k, v)
