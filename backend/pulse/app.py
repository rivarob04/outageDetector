"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from pulse import __version__
from pulse.config import Settings, get_settings
from pulse.logging import setup_logging
from pulse.runtime import Runtime

log = logging.getLogger("pulse.app")


def create_app(settings: Settings | None = None, *, start_background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = Runtime(settings)
        app.state.rt = rt
        await rt.start(background=start_background)
        log.info("pulse started", extra={"extra_fields": {"version": __version__}})
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(
        title="Pulse",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    from starlette.middleware.sessions import SessionMiddleware

    from pulse.api import health

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.ensure_secret_key(),
        session_cookie="pulse_session",
        max_age=30 * 24 * 3600,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    from pulse.api.security import SecurityHeaders

    app.add_middleware(SecurityHeaders, settings=settings)
    app.include_router(health.router)
    _mount_routers(app)
    _mount_static(app, settings)
    return app


def _mount_routers(app: FastAPI) -> None:
    """Hook for routers added in later phases (kept separate for readability)."""
    try:
        from pulse.api.routes import register
    except ImportError:  # pragma: no cover - early phases
        return
    register(app)


def _mount_static(app: FastAPI, settings: Settings) -> None:
    static = settings.resolved_static_dir()
    if static is None:

        @app.get("/", include_in_schema=False)
        async def no_ui() -> JSONResponse:
            return JSONResponse({"name": "pulse", "ui": "not built", "docs": "/api/docs"})

        return

    assets = static / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    index = static / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (static / path).resolve()
        if path and candidate.is_file() and _is_within(candidate, static):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False
