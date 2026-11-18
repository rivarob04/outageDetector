"""Probe specs and results (shared by the hub and remote agents)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ProbeKind = Literal["http", "dns", "tcp", "tls"]
ProbeError = Literal[
    "dns", "connect", "tls", "timeout", "http_status", "assertion", "ssrf_blocked", "other"
]


@dataclass(frozen=True)
class ProbeSpec:
    id: str  # "<service>:<kind>:<index>"
    service_id: str
    kind: ProbeKind
    url: str | None = None
    host: str | None = None
    port: int | None = None
    method: str = "GET"
    accept_status: tuple[int, ...] = ()
    keyword: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    timeout_s: float = 10.0
    interval_s: int = 60
    guarded: bool = False  # apply the SSRF guard (custom, user-supplied targets)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["accept_status"] = list(self.accept_status)
        d["headers"] = dict(self.headers)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProbeSpec:
        d = dict(d)
        d["accept_status"] = tuple(d.get("accept_status") or ())
        d["headers"] = tuple((d.get("headers") or {}).items())
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ProbeResult:
    probe_id: str
    ts: datetime
    ok: bool
    latency_ms: float | None = None
    dns_ms: float | None = None
    connect_ms: float | None = None
    tls_ms: float | None = None
    ttfb_ms: float | None = None
    status_code: int | None = None
    error: ProbeError | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProbeResult:
        d = dict(d)
        ts = d.get("ts")
        d["ts"] = datetime.fromisoformat(ts) if isinstance(ts, str) else datetime.now(UTC)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
