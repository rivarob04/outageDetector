from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from pulse.app import create_app
from pulse.config import Settings

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"


PG_URL = os.environ.get("PULSE_TEST_DATABASE_URL", "")


async def _reset_postgres(url: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


@pytest.fixture
async def settings(tmp_path: Path) -> Settings:
    if PG_URL:
        await _reset_postgres(PG_URL)
    return Settings(
        data_dir=tmp_path,
        database_url=PG_URL,
        secret_key="test-secret",
        log_json=False,
        static_dir=None,
        catalog_dir=CATALOG_DIR,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, start_background=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c
