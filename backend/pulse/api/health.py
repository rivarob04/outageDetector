from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pulse import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: migrations done and the database answers."""
    rt = request.app.state.rt
    ok = rt.started and await rt.db_ok()
    return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)
