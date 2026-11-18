"""SQLAlchemy Core table definitions. Keep in sync with Alembic migrations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store naive UTC, always return timezone-aware UTC (portable across SQLite/Postgres)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


BigId = BigInteger().with_variant(Integer(), "sqlite")

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

settings_t = Table(
    "settings",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", JSON, nullable=True),
    Column("updated_at", UTCDateTime(), nullable=True),
)

services_t = Table(
    "services",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("category", String(32), nullable=False),
    Column("source", String(16), nullable=False),  # catalog | custom | config
    Column("tracked", Boolean, nullable=False, default=False),
    Column("config", JSON, nullable=True),  # full ServiceDef for custom services
    Column("created_at", UTCDateTime(), nullable=False),
    Column("tracked_at", UTCDateTime(), nullable=True),
)

components_t = Table(
    "components",
    metadata,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("service_id", String(64), ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
    Column("ext_id", String(200), nullable=False),
    Column("name", String(300), nullable=False),
    Column("grp", String(300), nullable=True),
    Column("status", String(32), nullable=False),
    Column("region", String(64), nullable=True),
    Column("updated_at", UTCDateTime(), nullable=False),
    UniqueConstraint("service_id", "ext_id", name="uq_components_service_ext"),
)

incidents_t = Table(
    "incidents",
    metadata,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("service_id", String(64), ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
    Column("ext_id", String(300), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("title", Text, nullable=False),
    Column("impact", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    Column("url", Text, nullable=True),
    Column("started_at", UTCDateTime(), nullable=True),
    Column("resolved_at", UTCDateTime(), nullable=True),
    Column("scheduled_for", UTCDateTime(), nullable=True),
    Column("scheduled_until", UTCDateTime(), nullable=True),
    Column("components", JSON, nullable=True),
    Column("regions", JSON, nullable=True),
    Column("first_seen_at", UTCDateTime(), nullable=False),
    Column("last_seen_at", UTCDateTime(), nullable=False),
    UniqueConstraint("service_id", "ext_id", name="uq_incidents_service_ext"),
    Index("ix_incidents_service_resolved", "service_id", "resolved_at"),
)

incident_updates_t = Table(
    "incident_updates",
    metadata,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("incident_id", BigId, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
    Column("ext_id", String(300), nullable=False),
    Column("status", String(32), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    UniqueConstraint("incident_id", "ext_id", name="uq_incident_updates_incident_ext"),
)

feed_status_t = Table(
    "feed_status",
    metadata,
    Column(
        "service_id",
        String(64),
        ForeignKey("services.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("indicator", String(16), nullable=False),
    Column("description", Text, nullable=True),
    Column("ok", Boolean, nullable=False),
    Column("error", Text, nullable=True),
    Column("fetched_at", UTCDateTime(), nullable=False),
    Column("last_ok_at", UTCDateTime(), nullable=True),
)

probes_t = Table(
    "probes",
    metadata,
    Column("id", String(100), primary_key=True),
    Column("service_id", String(64), ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("target", Text, nullable=False),
    Column("config", JSON, nullable=False),  # ProbeSpec.to_dict()
    Column("enabled", Boolean, nullable=False, default=True),
)

probe_results_t = Table(
    "probe_results",
    metadata,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("probe_id", String(100), nullable=False),
    Column("agent_id", String(64), nullable=True),
    Column("ts", UTCDateTime(), nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("latency_ms", Float, nullable=True),
    Column("dns_ms", Float, nullable=True),
    Column("connect_ms", Float, nullable=True),
    Column("tls_ms", Float, nullable=True),
    Column("ttfb_ms", Float, nullable=True),
    Column("status_code", Integer, nullable=True),
    Column("error", String(16), nullable=True),
    Column("detail", JSON, nullable=True),
    Index("ix_probe_results_probe_ts", "probe_id", "ts"),
    Index("ix_probe_results_ts", "ts"),
)

rollups_t = Table(
    "rollups",
    metadata,
    Column("probe_id", String(100), primary_key=True),
    Column("agent_id", String(64), primary_key=True),  # "" = this hub
    Column("res", String(4), primary_key=True),  # 5m | 1d
    Column("bucket", UTCDateTime(), primary_key=True),
    Column("n", Integer, nullable=False),
    Column("n_ok", Integer, nullable=False),
    Column("avg_ms", Float, nullable=True),
    Column("p50_ms", Float, nullable=True),
    Column("p95_ms", Float, nullable=True),
    Index("ix_rollups_res_bucket", "res", "bucket"),
)

service_state_t = Table(
    "service_state",
    metadata,
    Column(
        "service_id",
        String(64),
        ForeignKey("services.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("state", String(20), nullable=False),
    Column("scope", String(24), nullable=False),
    Column("reasons", JSON, nullable=False),
    Column("advisories", JSON, nullable=False),
    Column("since", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    Column("pending_state", String(20), nullable=True),
    Column("pending_scope", String(24), nullable=True),
    Column("pending_count", Integer, nullable=False, default=0),
)

state_transitions_t = Table(
    "state_transitions",
    metadata,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("service_id", String(64), ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
    Column("from_state", String(20), nullable=True),
    Column("to_state", String(20), nullable=False),
    Column("from_scope", String(24), nullable=True),
    Column("scope", String(24), nullable=False),
    Column("reasons", JSON, nullable=False),
    Column("ts", UTCDateTime(), nullable=False),
    Index("ix_state_transitions_service_ts", "service_id", "ts"),
)

agents_t = Table(
    "agents",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("location", String(200), nullable=True),
    Column("lat", Float, nullable=True),
    Column("lon", Float, nullable=True),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("last_seen_at", UTCDateTime(), nullable=True),
    Column("version", String(32), nullable=True),
    Column("revoked", Boolean, nullable=False, default=False),
)

agent_nonces_t = Table(
    "agent_nonces",
    metadata,
    Column("nonce", String(64), primary_key=True),
    Column("ts", UTCDateTime(), nullable=False),
)

users_t = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(100), nullable=False, unique=True),
    Column("password_hash", String(300), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
)

notify_channels_t = Table(
    "notify_channels",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), nullable=False),
    Column("kind", String(16), nullable=False),  # apprise | webhook
    Column("url", Text, nullable=False),
    Column("secret", String(200), nullable=True),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", UTCDateTime(), nullable=False),
)

notify_rules_t = Table(
    "notify_rules",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "channel_id", Integer, ForeignKey("notify_channels.id", ondelete="CASCADE"), nullable=False
    ),
    Column("services", JSON, nullable=False),  # [] = all
    Column("categories", JSON, nullable=False),  # [] = all
    Column("min_state", String(20), nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("quiet_start", String(5), nullable=True),  # "22:00"
    Column("quiet_end", String(5), nullable=True),
    Column("tz", String(64), nullable=False, default="UTC"),
    Column("cooldown_s", Integer, nullable=False, default=600),
    Column("recovery", Boolean, nullable=False, default=True),
    Column("digest", Boolean, nullable=False, default=False),
    Column("enabled", Boolean, nullable=False, default=True),
)

notify_log_t = Table(
    "notify_log",
    metadata,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("channel_id", Integer, nullable=True),
    Column("service_id", String(64), nullable=True),
    Column("kind", String(16), nullable=False),
    Column("title", Text, nullable=True),
    Column("ts", UTCDateTime(), nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("error", Text, nullable=True),
    Index("ix_notify_log_ts", "ts"),
)

chatter_t = Table(
    "chatter",
    metadata,
    Column("service_id", String(64), primary_key=True),
    Column("source", String(16), primary_key=True),
    Column("bucket", UTCDateTime(), primary_key=True),
    Column("count", Integer, nullable=False),
)
