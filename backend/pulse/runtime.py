"""Process-wide runtime: owns the DB engine, store, catalog, monitor and integrations."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.api.auth import hash_password
from pulse.api.views import UptimeCache
from pulse.catalog.loader import Catalog, load_catalog
from pulse.config import Settings
from pulse.configfile import apply_config, find_config, load_config
from pulse.db.engine import make_engine
from pulse.db.migrate import upgrade_async
from pulse.db.store import Store
from pulse.events import EventBus
from pulse.monitor import Monitor
from pulse.probes.canary import canary_specs
from pulse.runtime_settings import apply_overrides

log = logging.getLogger("pulse.runtime")


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine = make_engine(settings)
        self.store = Store(self.engine)
        self.catalog: Catalog = load_catalog(settings.resolved_catalog_dir())
        self.bus = EventBus()
        self.monitor = Monitor(settings, self.store, self.catalog, self.bus)
        self.uptime = UptimeCache()
        self.started = False
        self.setup_code: str | None = None
        self.config_file: Path | None = None
        self.config_locked: set[str] = set()
        self.extras: list[Any] = []  # integrations with start()/stop()

    async def start(self, background: bool = True) -> None:
        s = self.settings
        s.ensure_secret_key()
        await upgrade_async(s)
        await self.store.sync_catalog(
            (svc.id, svc.name, svc.category) for svc in self.catalog.services.values()
        )
        overrides = await self.store.get_setting("overrides", {}) or {}
        try:
            apply_overrides(s, overrides)
        except ValueError as exc:
            log.error("ignoring invalid stored settings: %s", exc)
        self.config_file = find_config(s.config_file, s.data_dir)
        if self.config_file:
            cfg = load_config(self.config_file)
            apply_overrides(s, cfg.settings)
            self.config_locked = set(cfg.settings)
            await apply_config(self, cfg)
            log.info("applied config file %s", self.config_file)
        self.reconfigure()
        await self._bootstrap_admin()
        if s.demo:
            from pulse.demo import seed_demo

            await seed_demo(self)
        await self.monitor.start(run_jobs=background and not s.demo)
        if s.demo and background:
            from pulse.demo import start_demo_loop

            self.extras.append(start_demo_loop(self))
        await self._start_extras(background)
        self.started = True

    async def _start_extras(self, background: bool) -> None:
        """Hook for integrations added in later phases."""
        for name in ("pulse.integrations.bootstrap",):
            try:
                module = __import__(name, fromlist=["start"])
            except ModuleNotFoundError as exc:
                if exc.name != name:
                    raise
                continue
            await module.start(self, background)

    async def stop(self) -> None:
        for extra in reversed(self.extras):
            stop = getattr(extra, "stop", None)
            if stop:
                await stop()
        await self.monitor.stop()
        await self.bus.drain()
        await self.engine.dispose()

    def reconfigure(self) -> None:
        """Propagate (runtime-editable) settings to running components."""
        s, m = self.settings, self.monitor
        m.canary_specs = canary_specs(s.list_setting(s.canary_targets))
        m.ctx.public_resolvers = s.list_setting(s.dns_resolvers)
        m.ctx.allow_private = s.allow_private_targets
        m.vcfg = type(m.vcfg)(
            fail_threshold=s.fail_threshold, recover_threshold=s.recover_threshold
        )

    async def _bootstrap_admin(self) -> None:
        s = self.settings
        if await self.store.user_count() > 0:
            return
        if s.admin_username and s.admin_password:
            await self.store.create_user(s.admin_username, hash_password(s.admin_password))
            log.info("created admin user '%s' from environment", s.admin_username)
            return
        if s.trusted_auth_header:
            return
        self.setup_code = secrets.token_hex(4)
        log.warning(
            "No admin account yet. Open the UI and use setup code %s to create one "
            "(or set PULSE_ADMIN_USERNAME / PULSE_ADMIN_PASSWORD).",
            self.setup_code,
        )

    async def db_ok(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            log.exception("db check failed")
            return False
