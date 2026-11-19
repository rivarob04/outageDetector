"""Catalog schema: one YAML file per provider in ``catalog/``."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal[
    "cloud",
    "cdn",
    "devtools",
    "ai",
    "comms",
    "productivity",
    "payments",
    "streaming",
    "gaming",
    "social",
    "smarthome",
    "network",
    "custom",
]

CATEGORY_LABELS: dict[str, str] = {
    "cloud": "Cloud",
    "cdn": "CDN & Edge",
    "devtools": "Dev tools",
    "ai": "AI",
    "comms": "Comms",
    "productivity": "Productivity",
    "payments": "Payments",
    "streaming": "Streaming",
    "gaming": "Gaming",
    "social": "Social",
    "smarthome": "Smart home",
    "network": "Network",
    "custom": "My services",
}

AdapterKind = Literal[
    "statuspage",
    "statusio",
    "aws",
    "gcp",
    "gworkspace",
    "slack",
    "apple",
    "heroku",
    "rss",
    "cogeco",
    "mymaps",
]

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedConfig(_Strict):
    adapter: AdapterKind
    url: str
    components_include: list[str] = Field(default_factory=list)
    components_exclude: list[str] = Field(default_factory=list)
    # Region mapping: regex on component name -> region id, or the special
    # value "auto" to let the adapter infer (AWS/GCP regions, Cloudflare IATA codes).
    regions: dict[str, str] | Literal["auto"] | None = None
    options: dict[str, str] = Field(default_factory=dict)


class ProbeConfig(_Strict):
    kind: Literal["http", "dns", "tcp", "tls"]
    url: str | None = None  # http
    host: str | None = None  # dns / tcp / tls
    port: int | None = None
    method: Literal["GET", "HEAD"] = "GET"
    # 2xx/3xx always count as up; list extra codes that also mean "up" (e.g. 401 on an API)
    accept_status: list[int] = Field(default_factory=list)
    keyword: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    interval_s: int | None = None
    timeout_s: float | None = None

    @model_validator(mode="after")
    def _target(self) -> ProbeConfig:
        if self.kind == "http" and not self.url:
            raise ValueError("http probe needs url")
        if self.kind in ("dns", "tcp", "tls") and not self.host:
            raise ValueError(f"{self.kind} probe needs host")
        if self.kind == "tcp" and not self.port:
            raise ValueError("tcp probe needs port")
        return self


class ServiceDef(_Strict):
    id: str
    name: str
    category: Category
    icon: str | None = None  # simple-icons slug
    homepage: str | None = None
    status_page: str | None = None
    description: str | None = None
    feed: FeedConfig | None = None
    probes: list[ProbeConfig] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    depends_on_sources: list[str] = Field(default_factory=list)
    chatter_keywords: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("id must be a lowercase slug")
        return v

    @model_validator(mode="after")
    def _has_signal(self) -> ServiceDef:
        if self.feed is None and not self.probes:
            raise ValueError("service needs a feed or at least one probe")
        return self

    @property
    def signal(self) -> str:
        if self.feed and self.probes:
            return "feed+checks"
        return "feed" if self.feed else "checks"


class Region(_Strict):
    id: str
    name: str
    provider: str
    lat: float
    lon: float


class VerificationEntry(BaseModel):
    service_id: str
    checked_at: str
    feed_url: str | None = None
    feed_status: int | None = None
    feed_ok: bool | None = None
    feed_parsed: bool | None = None
    feed_error: str | None = None
    probes: list[dict[str, object]] = Field(default_factory=list)
    verified: bool = False
