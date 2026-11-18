"""Run Alembic migrations programmatically (used at startup and by `pulse migrate`)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from pulse.config import Settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(settings: Settings) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.db_url.replace("%", "%%"))
    return cfg


def upgrade(settings: Settings, revision: str = "head") -> None:
    if settings.is_sqlite:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(settings), revision)


def downgrade(settings: Settings, revision: str) -> None:
    command.downgrade(alembic_config(settings), revision)


async def upgrade_async(settings: Settings) -> None:
    await asyncio.to_thread(upgrade, settings)
