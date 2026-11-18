"""REST API v1 + server-sent events."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from pulse import __version__
from pulse.api import views
from pulse.api.auth import (
    LoginLimiter,
    client_ip,
    current_user,
    hash_password,
    require_admin,
    require_view,
    start_session,
    verify_password,
)
from pulse.catalog.schema import CATEGORY_LABELS, Category, FeedConfig, ProbeConfig, ServiceDef
from pulse.runtime_settings import EDITABLE, apply_overrides
from pulse.services import slugify

public = APIRouter(prefix="/api/v1", dependencies=[Depends(require_view)], tags=["public"])
admin = APIRouter(prefix="/api/v1", tags=["admin"])
auth = APIRouter(prefix="/api/v1/auth", tags=["auth"])
limiter = LoginLimiter()


def rt_of(request: Request) -> Any:
    return request.app.state.rt


# ============================================================== public
@public.get("/summary")
async def summary(request: Request) -> dict[str, Any]:
    """Everything the dashboard needs in one call."""
    rt = rt_of(request)
    mon = rt.monitor
    uptime = await rt.uptime.get(rt)
    active = await rt.store.active_incidents(list(mon.services))
    by_svc: dict[str, list[dict[str, Any]]] = {}
    for inc in active:
        if inc["kind"] == "maintenance" and inc["status"] == "scheduled":
            continue
        by_svc.setdefault(inc["service_id"], []).append(inc)
    inc_days = await _incident_days(rt, list(mon.services))
    services = []
    for sid, svc in mon.services.items():
        series = views.uptime_series(uptime.get(sid, {}), inc_days.get(sid, set()))
        services.append(
            {
                **views.service_base(svc, mon.sources.get(sid, "catalog")),
                **views.verdict_dict(mon.verdicts.get(sid)),
                "last_checked": views.iso(mon.last_checked(sid)),
                "sparkline": mon.sparkline(sid),
                "uptime": series,
                "uptime_pct": views.uptime_pct(series),
                "incidents": [
                    {
                        "id": i["id"],
                        "title": i["title"],
                        "impact": i["impact"],
                        "kind": i["kind"],
                        "url": i["url"],
                        "started_at": views.iso(i["started_at"]),
                    }
                    for i in by_svc.get(sid, [])
                ],
            }
        )
    counts: dict[str, int] = {}
    for s in services:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "demo": rt.settings.demo,
        "connectivity": mon.connectivity(),
        "counts": counts,
        "categories": CATEGORY_LABELS,
        "services": services,
    }


async def _incident_days(rt: Any, service_ids: list[str]) -> dict[str, set[str]]:
    since = datetime.now(UTC) - timedelta(days=90)
    rows, _ = await rt.store.list_incidents(kind="incident", limit=5000, since=since)
    out: dict[str, set[str]] = {}
    today = datetime.now(UTC).date()
    for inc in rows:
        if inc["service_id"] not in service_ids or inc["impact"] in ("none", "maintenance"):
            continue
        start = (inc["started_at"] or inc["first_seen_at"]).date()
        end = inc["resolved_at"].date() if inc["resolved_at"] else today
        d = start
        while d <= end and (d - start).days < 90:
            out.setdefault(inc["service_id"], set()).add(d.isoformat())
            d += timedelta(days=1)
    return out


@public.get("/services/{service_id}")
async def service_detail(service_id: str, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    mon = rt.monitor
    row = await rt.store.get_service(service_id)
    svc = mon.services.get(service_id) or (rt.catalog.get(service_id) if row else None)
    if row is None or svc is None:
        raise HTTPException(404, "unknown service")
    specs = mon.specs.get(service_id, [])
    latest = await rt.store.latest_results([s.id for s in specs])
    incidents, _ = await rt.store.list_incidents(
        service_id=service_id, limit=25, tracked_only=False
    )
    updates = await rt.store.incident_updates([i["id"] for i in incidents])
    feed = (await rt.store.feed_status()).get(service_id)
    uptime = (await rt.uptime.get(rt)).get(service_id, {})
    inc_days = (await _incident_days(rt, [service_id])).get(service_id, set())
    series = views.uptime_series(uptime, inc_days)
    agents = []
    for aid, agent in mon.agents.items():
        results = []
        for s in specs:
            v = mon.agent_views.get((aid, s.id))
            if v:
                results.append(
                    {
                        "probe_id": s.id,
                        "ok": v.ok,
                        "latency_ms": v.latency_ms,
                        "ts": views.iso(v.ts),
                        "consecutive_failures": v.consecutive_failures,
                    }
                )
        if results:
            agents.append(
                {
                    "id": aid,
                    "name": agent["name"],
                    "location": agent.get("location"),
                    "last_seen_at": views.iso(agent.get("last_seen_at")),
                    "results": results,
                }
            )
    return {
        **views.service_base(
            svc,
            row["source"],
            rt.catalog.is_verified(service_id) if row["source"] == "catalog" else None,
        ),
        "tracked": row["tracked"],
        "description": svc.description,
        "notes": svc.notes,
        "config": svc.model_dump(mode="json") if row["source"] != "catalog" else None,
        **views.verdict_dict(mon.verdicts.get(service_id)),
        "last_checked": views.iso(mon.last_checked(service_id)),
        "feed": {
            "url": svc.feed.url if svc.feed else None,
            "adapter": svc.feed.adapter if svc.feed else None,
            "indicator": feed["indicator"] if feed else None,
            "description": feed["description"] if feed else None,
            "ok": feed["ok"] if feed else None,
            "error": feed["error"] if feed else None,
            "fetched_at": views.iso(feed["fetched_at"]) if feed else None,
        }
        if svc.feed
        else None,
        "components": [
            {"name": c["name"], "group": c["grp"], "status": c["status"], "region": c["region"]}
            for c in await rt.store.components(service_id)
        ],
        "probes": [{**s.to_dict(), "latest": _result_dict(latest.get(s.id))} for s in specs],
        "incidents": [views.incident_dict(i, updates.get(i["id"], [])) for i in incidents],
        "uptime": series,
        "uptime_pct": views.uptime_pct(series),
        "agents": agents,
        "transitions": [
            {
                "from_state": t["from_state"],
                "to_state": t["to_state"],
                "scope": t["scope"],
                "reasons": t["reasons"],
                "ts": views.iso(t["ts"]),
            }
            for t in await rt.store.transitions(service_id, limit=30)
        ],
    }


def _result_dict(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        k: (views.iso(v) if isinstance(v, datetime) else v)
        for k, v in r.items()
        if k not in ("id", "agent_id")
    }


@public.get("/services/{service_id}/latency")
async def latency(
    service_id: str,
    request: Request,
    range_: Literal["24h", "7d", "30d", "90d"] = Query("24h", alias="range"),
    agent: str = "",
) -> dict[str, Any]:
    """Latency series per probe: raw points for 24h, 5-minute rollups for 7d, daily beyond."""
    rt = rt_of(request)
    specs = [s for s in rt.monitor.specs.get(service_id, []) if s.kind != "tls"]
    if not specs and service_id not in rt.monitor.services:
        raise HTTPException(404, "unknown service")
    hours = {"24h": 24, "7d": 168, "30d": 720, "90d": 2160}[range_]
    since = datetime.now(UTC) - timedelta(hours=hours)
    out: dict[str, Any] = {"range": range_, "probes": []}
    for s in specs:
        if range_ == "24h" and not agent:
            rows = await rt.store.recent_results(since, probe_ids=[s.id])
            pts = [
                {
                    "ts": views.iso(r["ts"]),
                    "ok": r["ok"],
                    "latency_ms": r["latency_ms"],
                    "dns_ms": r["dns_ms"],
                    "connect_ms": r["connect_ms"],
                    "tls_ms": r["tls_ms"],
                    "ttfb_ms": r["ttfb_ms"],
                    "error": r["error"],
                }
                for r in rows
            ]
        else:
            res = "5m" if hours <= 168 else "1d"
            rows = await rt.store.rollups([s.id], res, since, agent_id=agent)
            pts = [
                {
                    "ts": views.iso(r["bucket"]),
                    "ok": r["n_ok"] == r["n"],
                    "n": r["n"],
                    "n_ok": r["n_ok"],
                    "latency_ms": r["p50_ms"],
                    "p95_ms": r["p95_ms"],
                }
                for r in rows
            ]
        out["probes"].append({"id": s.id, "kind": s.kind, "target": s.url or s.host, "points": pts})
    return out


@public.get("/incidents")
async def incidents(
    request: Request,
    service: str | None = None,
    status: Literal["active", "upcoming", "resolved"] | None = None,
    kind: Literal["incident", "maintenance"] | None = None,
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    rt = rt_of(request)
    since = datetime.now(UTC) - timedelta(days=days)
    rows, total = await rt.store.list_incidents(service, status, kind, True, limit, offset, since)
    updates = await rt.store.incident_updates([r["id"] for r in rows])
    return {
        "total": total,
        "items": [views.incident_dict(r, updates.get(r["id"], [])) for r in rows],
    }


@public.get("/incidents/stats")
async def incident_stats(request: Request, days: int = Query(90, ge=1, le=365)) -> dict[str, Any]:
    rt = rt_of(request)
    since = datetime.now(UTC) - timedelta(days=days)
    rows, _ = await rt.store.list_incidents(kind="incident", limit=10000, since=since)
    stats = views.mttr(rows)
    lead = getattr(rt, "lead_times", None)
    if lead is not None:
        for sid, item in (await lead(days)).items():
            stats.setdefault(
                sid,
                {
                    "service_id": sid,
                    "service_name": item["service_name"],
                    "count": 0,
                    "mttr_s": None,
                    "median_s": None,
                },
            )
            stats[sid]["lead"] = item
    return {"days": days, "providers": sorted(stats.values(), key=lambda s: -s["count"])}


@public.get("/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    rows = {r["id"]: r for r in await rt.store.list_services()}
    items = []
    for sid, svc in sorted(rt.catalog.services.items(), key=lambda kv: kv[1].name.lower()):
        row = rows.get(sid)
        items.append(
            {
                **views.service_base(svc, "catalog", rt.catalog.is_verified(sid)),
                "tracked": bool(row and row["tracked"]),
                "notes": svc.notes,
            }
        )
    custom = [
        {
            **views.service_base(ServiceDef.model_validate(r["config"]), r["source"]),
            "tracked": r["tracked"],
        }
        for r in rows.values()
        if r["source"] != "catalog" and r.get("config")
    ]
    return {"categories": CATEGORY_LABELS, "items": items, "custom": custom}


@public.get("/connectivity")
async def connectivity(request: Request) -> dict[str, Any]:
    return dict(rt_of(request).monitor.connectivity())


@public.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events: state, incident, connectivity, agent (+ ping every 15s)."""
    rt = rt_of(request)

    async def stream() -> Any:
        yield "retry: 5000\n\n"
        async with rt.bus.subscribe() as q:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"event: {ev.type}\ndata: {json.dumps(ev.data, default=str)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================== auth
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class SetupBody(Credentials):
    password: str = Field(min_length=10, max_length=200)
    setup_code: str = ""


@auth.get("/me")
async def me(
    request: Request, user: dict[str, Any] | None = Depends(current_user)
) -> dict[str, Any]:
    rt = rt_of(request)
    return {
        "user": {"username": user["username"], "via": user["via"]} if user else None,
        "csrf": request.session.get("csrf") if user and user["via"] == "session" else None,
        "setup_required": await rt.store.user_count() == 0 and not rt.settings.trusted_auth_header,
        "public_view": rt.settings.public_view,
        "demo": rt.settings.demo,
        "version": __version__,
    }


@auth.post("/setup")
async def setup(body: SetupBody, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    if await rt.store.user_count() > 0:
        raise HTTPException(409, "setup already completed")
    if not rt.setup_code or body.setup_code.strip() != rt.setup_code:
        raise HTTPException(403, "invalid setup code (see the container logs)")
    uid = await rt.store.create_user(body.username, hash_password(body.password))
    rt.setup_code = None
    csrf = start_session(request, uid)
    return {"ok": True, "csrf": csrf}


@auth.post("/login")
async def login(body: Credentials, request: Request) -> JSONResponse:
    rt = rt_of(request)
    ip = client_ip(request)
    if limiter.blocked(ip):
        raise HTTPException(429, "too many failed logins; try again in a few minutes")
    user = await rt.store.get_user(body.username)
    if user is None or not verify_password(body.password, user["password_hash"]):
        limiter.fail(ip)
        raise HTTPException(401, "invalid username or password")
    limiter.reset(ip)
    csrf = start_session(request, user["id"])
    return JSONResponse({"ok": True, "csrf": csrf})


@auth.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


@admin.post("/auth/password")
async def change_password(
    body: PasswordChange, request: Request, user: dict[str, Any] = Depends(require_admin)
) -> dict[str, bool]:
    rt = rt_of(request)
    row = await rt.store.get_user_by_id(user["id"])
    if row is None or not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(403, "current password is wrong")
    await rt.store.set_password(user["id"], hash_password(body.new_password))
    return {"ok": True}


# ============================================================== admin: services
@admin.post("/services/{service_id}/track", dependencies=[Depends(require_admin)])
async def track(service_id: str, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    try:
        await rt.monitor.track(service_id)
    except KeyError:
        raise HTTPException(404, "unknown service") from None
    rt.uptime.invalidate()
    return {"ok": True, "tracked": True}


@admin.delete("/services/{service_id}/track", dependencies=[Depends(require_admin)])
async def untrack(service_id: str, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    if await rt.store.get_service(service_id) is None:
        raise HTTPException(404, "unknown service")
    await rt.monitor.untrack(service_id)
    return {"ok": True, "tracked": False}


@admin.post("/services/{service_id}/check", dependencies=[Depends(require_admin)])
async def check_now(service_id: str, request: Request) -> dict[str, bool]:
    rt_of(request).monitor.check_now(service_id)
    return {"ok": True}


class CustomService(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: Category = "custom"
    homepage: str | None = None
    status_page: str | None = None
    description: str | None = None
    feed: FeedConfig | None = None
    probes: list[ProbeConfig] = Field(default_factory=list, max_length=10)
    depends_on: list[str] = Field(default_factory=list)


@admin.post("/services", dependencies=[Depends(require_admin)], status_code=201)
async def create_service(body: CustomService, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    base = slugify(body.name)
    sid, n = base, 2
    while await rt.store.get_service(sid) is not None or sid in rt.catalog.services:
        sid, n = f"{base}-{n}", n + 1
    return await _save_custom(rt, sid, body, "custom")


@admin.put("/services/{service_id}", dependencies=[Depends(require_admin)])
async def update_service(service_id: str, body: CustomService, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    row = await rt.store.get_service(service_id)
    if row is None:
        raise HTTPException(404, "unknown service")
    if row["source"] != "custom":
        raise HTTPException(
            409,
            "only custom services can be edited (config-file services are managed in pulse.yaml)",
        )
    return await _save_custom(rt, service_id, body, "custom")


async def _save_custom(rt: Any, sid: str, body: CustomService, source: str) -> dict[str, Any]:
    try:
        svc = ServiceDef.model_validate({"id": sid, **body.model_dump(exclude_none=True)})
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json())) from None
    for dep in svc.depends_on:
        if dep.split(":", 1)[0] not in rt.catalog.services:
            raise HTTPException(422, f"unknown dependency {dep}")
    await rt.store.save_custom_service(
        sid, svc.name, svc.category, svc.model_dump(mode="json"), source
    )
    await rt.monitor.track(sid)
    return {"ok": True, "id": sid}


@admin.delete("/services/{service_id}", dependencies=[Depends(require_admin)])
async def delete_service(service_id: str, request: Request) -> dict[str, bool]:
    rt = rt_of(request)
    row = await rt.store.get_service(service_id)
    if row is None:
        raise HTTPException(404, "unknown service")
    if row["source"] == "catalog":
        raise HTTPException(409, "catalog services can only be untracked")
    await rt.monitor.remove(service_id)
    return {"ok": True}


# ============================================================== admin: settings & config
@admin.get("/settings", dependencies=[Depends(require_admin)])
async def get_settings(request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    s = rt.settings
    overrides = await rt.store.get_setting("overrides", {}) or {}
    return {
        "editable": {k: getattr(s, k) for k in EDITABLE},
        "overrides": overrides,
        "locked": sorted(rt.config_locked),
        "server": {
            "version": __version__,
            "database": "sqlite" if s.is_sqlite else "postgres",
            "data_dir": str(s.data_dir),
            "base_url": s.base_url,
            "trusted_auth_header": s.trusted_auth_header or None,
            "mqtt": bool(s.mqtt_url),
            "metrics": s.metrics_enabled,
            "demo": s.demo,
            "config_file": str(rt.config_file) if rt.config_file else None,
            "features": {
                "chatter": s.feature_chatter,
                "anomaly": s.feature_anomaly,
                "correlation": s.feature_correlation,
            },
        },
        "stats": await rt.store.table_counts(),
    }


@admin.put("/settings", dependencies=[Depends(require_admin)])
async def put_settings(body: dict[str, Any], request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    unknown = set(body) - set(EDITABLE)
    if unknown:
        raise HTTPException(422, f"not editable: {sorted(unknown)}")
    locked = set(body) & rt.config_locked
    if locked:
        raise HTTPException(409, f"set in the config file: {sorted(locked)}")
    overrides = dict(await rt.store.get_setting("overrides", {}) or {})
    overrides.update(body)
    try:
        apply_overrides(rt.settings, overrides)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from None
    await rt.store.set_setting("overrides", overrides)
    rt.reconfigure()
    return {"ok": True}


@admin.get("/config/export", dependencies=[Depends(require_admin)])
async def export_config(request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    rows = await rt.store.list_services()
    return {
        "pulse_export": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "track": sorted(r["id"] for r in rows if r["tracked"] and r["source"] == "catalog"),
        "custom": [r["config"] for r in rows if r["source"] == "custom" and r.get("config")],
        "settings": await rt.store.get_setting("overrides", {}) or {},
    }


class ImportBody(BaseModel):
    pulse_export: int = 1
    track: list[str] = Field(default_factory=list)
    custom: list[dict[str, Any]] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    replace: bool = False


@admin.post("/config/import", dependencies=[Depends(require_admin)])
async def import_config(body: ImportBody, request: Request) -> dict[str, Any]:
    rt = rt_of(request)
    unknown = [s for s in body.track if s not in rt.catalog.services]
    if unknown:
        raise HTTPException(422, f"unknown catalog services: {unknown}")
    customs = []
    for c in body.custom:
        try:
            customs.append(ServiceDef.model_validate(c))
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json())) from None
    if body.replace:
        for r in await rt.store.list_services(tracked=True):
            if r["source"] == "catalog" and r["id"] not in body.track:
                await rt.monitor.untrack(r["id"])
    for sid in body.track:
        await rt.monitor.track(sid)
    for svc in customs:
        await rt.store.save_custom_service(
            svc.id, svc.name, svc.category, svc.model_dump(mode="json"), "custom"
        )
        await rt.monitor.track(svc.id)
    if body.settings:
        await put_settings(body.settings, request)
    rt.uptime.invalidate()
    return {"ok": True, "tracked": len(body.track), "custom": len(customs)}


def register(app: FastAPI) -> None:
    app.include_router(auth)
    app.include_router(public)
    app.include_router(admin)
    for mod in ("pulse.api.agents", "pulse.api.integrations", "pulse.api.notify"):
        try:
            module = __import__(mod, fromlist=["register"])
        except ModuleNotFoundError as exc:
            if exc.name != mod:
                raise
            continue
        module.register(app)
