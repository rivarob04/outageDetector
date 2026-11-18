"""SQLAlchemy Core table definitions. Keep in sync with Alembic migrations."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table

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
    Column("updated_at", DateTime(timezone=True), nullable=True),
)
