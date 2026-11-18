"""Process-wide runtime: owns the DB engine and background services."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.config import Settings
from pulse.db.engine import make_engine
from pulse.db.migrate import upgrade_async

log = logging.getLogger("pulse.runtime")


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine = make_engine(settings)
        self.started = False

    async def start(self, background: bool = True) -> None:
        self.settings.ensure_secret_key()
        await upgrade_async(self.settings)
        self.started = True

    async def stop(self) -> None:
        await self.engine.dispose()

    async def db_ok(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            log.exception("db check failed")
            return False
