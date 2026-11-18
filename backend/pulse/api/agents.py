"""Vantage agents: admin management, the signed agent API, and the map API."""

from __future__ import annotations

import json
import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from pulse import __version__
from pulse.agent.signing import SignatureError, make_token, verify
from pulse.api.auth import require_admin, require_view
from pulse.api.views import iso
from pulse.probes.ssrf import is_blocked_ip
from pulse.probes.types import ProbeResult, ProbeSpec
from pulse.verdict.engine import STATE_RANK

admin = APIRouter(prefix="/api/v1/agents", tags=["agents"], dependencies=[Depends(require_admin)])
agent_api = APIRouter(prefix="/api/v1/agent", tags=["agent protocol"])
map_api = APIRouter(prefix="/api/v1", tags=["public"], dependencies=[Depends(require_view)])

MAX_RESULTS = 2000
COMPONENT_STATE = {
    "degraded": "degraded",
    "partial_outage": "partial_outage",
    "major_outage": "major_outage",
    "maintenance": "maintenance",
    "operational": "operational",
}
IMPACT_STATE = {
    "minor": "degraded",
    "major": "partial_outage",
    "critical": "major_outage",
    "maintenance": "maintenance",
}


def _rt(request: Request) -> Any:
    return request.app.state.rt


# ------------------------------------------------------------------ admin
class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


def agent_dict(rt: Any, a: dict[str, Any]) -> dict[str, Any]:
    fresh = rt.monitor.agent_fresh(rt.monitor.agents.get(a["id"], a))
    return {
        "id": a["id"],
        "name": a["name"],
        "location": a["location"],
        "lat": a["lat"],
        "lon": a["lon"],
        "created_at": iso(a["created_at"]),
        "last_seen_at": iso(a["last_seen_at"]),
        "version": a.get("version"),
        "revoked": a["revoked"],
        "fresh": fresh,
    }


@admin.get("")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    rt = _rt(request)
    return [agent_dict(rt, a) for a in await rt.store.list_agents()]


@admin.post("", status_code=201)
async def create_agent(body: AgentIn, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    agent_id = secrets.token_hex(6)
    await rt.store.create_agent(agent_id, body.name, body.location, body.lat, body.lon)
    await rt.monitor.load_agents()
    hub = rt.settings.base_url or str(request.base_url).rstrip("/")
    return {
        "id": agent_id,
        "token": make_token(rt.settings.ensure_secret_key(), agent_id),
        "hub_url": hub,
        "note": "The token is shown once. Rotating PULSE_SECRET_KEY invalidates every agent token.",
    }


@admin.patch("/{agent_id}")
async def update_agent(agent_id: str, body: AgentIn, request: Request) -> dict[str, bool]:
    rt = _rt(request)
    if await rt.store.get_agent(agent_id) is None:
        raise HTTPException(404, "unknown agent")
    await rt.store.update_agent(agent_id, **body.model_dump())
    await rt.monitor.load_agents()
    return {"ok": True}


@admin.post("/{agent_id}/revoke")
async def revoke_agent(agent_id: str, request: Request) -> dict[str, bool]:
    rt = _rt(request)
    if await rt.store.get_agent(agent_id) is None:
        raise HTTPException(404, "unknown agent")
    await rt.store.update_agent(agent_id, revoked=True)
    await rt.monitor.load_agents()
    rt.monitor.dirty.update(rt.monitor.services)
    return {"ok": True}


@admin.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request) -> dict[str, bool]:
    rt = _rt(request)
    await rt.store.delete_agent(agent_id)
    await rt.monitor.load_agents()
    rt.monitor.dirty.update(rt.monitor.services)
    return {"ok": True}


# ------------------------------------------------------------------ agent protocol
async def authenticate(request: Request) -> dict[str, Any]:
    rt = _rt(request)
    body = await request.body()
    try:
        v = verify(
            rt.settings.ensure_secret_key(),
            dict(request.headers),
            request.method,
            request.url.path,
            body,
        )
    except SignatureError as exc:
        raise HTTPException(401, str(exc)) from None
    agent = await rt.store.get_agent(v.agent_id)
    if agent is None or agent["revoked"]:
        raise HTTPException(403, "agent unknown or revoked")
    if not await rt.store.use_nonce(v.nonce):
        raise HTTPException(409, "replayed request")
    return agent


def agent_safe(spec: ProbeSpec) -> bool:
    """Agents never get user-supplied probes that point at private/LAN names."""
    if not spec.guarded:
        return True
    from urllib.parse import urlsplit

    host = (urlsplit(spec.url).hostname if spec.url else spec.host) or ""
    if "." not in host or host.endswith((".local", ".lan", ".home.arpa", ".internal")):
        return False
    try:
        return not is_blocked_ip(host)
    except ValueError:
        return True  # a public-looking hostname


@agent_api.get("/probes")
async def agent_probes(request: Request) -> dict[str, Any]:
    agent = await authenticate(request)
    rt = _rt(request)
    specs = [s for specs in rt.monitor.specs.values() for s in specs if agent_safe(s)]
    return {
        "agent": {"id": agent["id"], "name": agent["name"]},
        "push_interval_s": rt.settings.agent_push_interval_s,
        "probes": [s.to_dict() for s in specs],
        "hub_version": __version__,
    }


@agent_api.post("/results")
async def agent_results(request: Request) -> dict[str, Any]:
    agent = await authenticate(request)
    rt = _rt(request)
    try:
        payload = json.loads(await request.body() or b"{}")
        raw = payload.get("results", [])
        if not isinstance(raw, list) or len(raw) > MAX_RESULTS:
            raise ValueError(f"results must be a list of at most {MAX_RESULTS} items")
        known = {s.id: s.service_id for specs in rt.monitor.specs.values() for s in specs}
        results = [ProbeResult.from_dict(r) for r in raw if r.get("probe_id") in known]
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, f"invalid payload: {exc}") from None
    now = datetime.now(UTC)
    for r in results:  # never trust agent clocks for ordering beyond the skew window
        if r.ts > now + timedelta(minutes=5) or r.ts < now - timedelta(hours=2):
            r.ts = now
    offline = bool(payload.get("offline"))
    info = payload.get("agent") or {}
    await rt.monitor.record_agent_results(
        agent["id"],
        results,
        offline=offline,
        version=str(info.get("version") or "")[:32] or None,
        location=info.get("location"),
    )
    return {"ok": True, "accepted": len(results)}


# ------------------------------------------------------------------ map
@map_api.get("/map")
async def map_view(request: Request, at: datetime | None = None) -> dict[str, Any]:
    """Regions with their worst state, agents and this host. ``at`` replays the last 24h."""
    rt = _rt(request)
    mon = rt.monitor
    now = datetime.now(UTC)
    if at is not None:
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        at = max(now - timedelta(hours=24), min(at, now))
    replay = at is not None and now - at > timedelta(minutes=2)
    regions: dict[str, dict[str, Any]] = {}

    def add(region_id: str, state: str, sid: str, what: str) -> None:
        reg = rt.catalog.regions.get(region_id)
        if reg is None or sid not in mon.services:
            return
        r = regions.setdefault(
            region_id,
            {
                "id": region_id,
                "name": reg.name,
                "provider": reg.provider,
                "lat": reg.lat,
                "lon": reg.lon,
                "state": "operational",
                "items": [],
            },
        )
        if STATE_RANK.get(state, 0) > STATE_RANK.get(r["state"], 0):
            r["state"] = state
        if state != "operational":
            r["items"].append(
                {
                    "service_id": sid,
                    "service_name": mon.services[sid].name,
                    "state": state,
                    "what": what,
                }
            )

    if not replay:
        for c in await rt.store.region_components():
            add(
                c["region"], COMPONENT_STATE.get(c["status"], "unknown"), c["service_id"], c["name"]
            )
        # Show every known region of watched providers, even when healthy.
        providers = {svc_provider(sid) for sid in mon.services}
        for rid, reg in rt.catalog.regions.items():
            if reg.provider in providers:
                regions.setdefault(
                    rid,
                    {
                        "id": rid,
                        "name": reg.name,
                        "provider": reg.provider,
                        "lat": reg.lat,
                        "lon": reg.lon,
                        "state": "operational",
                        "items": [],
                    },
                )
    point: datetime = at if (replay and at is not None) else now
    rows, _ = await rt.store.list_incidents(limit=2000, since=point - timedelta(days=30))
    for inc in rows:
        start = inc["started_at"] or inc["first_seen_at"]
        if start > point or (inc["resolved_at"] and inc["resolved_at"] <= point):
            continue
        if inc["kind"] == "maintenance" and inc["status"] == "scheduled" and not replay:
            continue
        state = IMPACT_STATE.get(inc["impact"], "degraded")
        for rid in inc.get("regions") or []:
            add(rid, state, inc["service_id"], inc["title"])
    agents = await _agents_at(rt, point if replay else None)
    host = None
    if rt.settings.host_location:
        try:
            lat, lon = (float(x) for x in rt.settings.host_location.split(","))
            host = {"lat": lat, "lon": lon, "name": "This Pulse", "offline": mon.canary.offline}
        except ValueError:
            host = None
    return {
        "at": point.isoformat(),
        "replay": replay,
        "style_url": rt.settings.map_style_url or None,
        "providers": sorted({r["provider"] for r in regions.values()}),
        "regions": sorted(regions.values(), key=lambda r: -STATE_RANK.get(r["state"], 0)),
        "agents": agents,
        "host": host,
    }


def svc_provider(service_id: str) -> str:
    return {"aws": "aws", "google-cloud": "gcp", "cloudflare": "cloudflare"}.get(service_id, "")


async def _agents_at(rt: Any, at: datetime | None) -> list[dict[str, Any]]:
    mon = rt.monitor
    out = []
    probe_names = {
        s.id: (sid, mon.services[sid].name)
        for sid, specs in mon.specs.items()
        for s in specs
        if sid in mon.services
    }
    replay_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if at is not None:
        for r in await rt.store.recent_results(at - timedelta(minutes=10), agent=True):
            if r["ts"] <= at:
                replay_rows[r["agent_id"]].append(r)
    for aid, a in mon.agents.items():
        if a.get("revoked") or a.get("lat") is None:
            continue
        failing: dict[str, str] = {}
        ok = 0
        lat_vals: list[float] = []
        if at is None:
            for (agent_id, pid), v in mon.agent_views.items():
                if agent_id != aid or pid not in probe_names:
                    continue
                if v.consecutive_failures >= rt.settings.fail_threshold:
                    failing[probe_names[pid][0]] = probe_names[pid][1]
                else:
                    ok += 1
                    if v.latency_ms:
                        lat_vals.append(v.latency_ms)
            fresh = mon.agent_fresh(a)
        else:
            latest: dict[str, dict[str, Any]] = {}
            for r in replay_rows.get(aid, []):
                latest[r["probe_id"]] = r
            for pid, r in latest.items():
                if pid not in probe_names:
                    continue
                if r["ok"]:
                    ok += 1
                else:
                    failing[probe_names[pid][0]] = probe_names[pid][1]
            fresh = bool(latest)
        out.append(
            {
                "id": aid,
                "name": a["name"],
                "location": a.get("location"),
                "lat": a["lat"],
                "lon": a["lon"],
                "fresh": fresh,
                "last_seen_at": iso(a.get("last_seen_at")),
                "ok_checks": ok,
                "failing": [
                    {"service_id": k, "service_name": v} for k, v in sorted(failing.items())
                ],
                "median_ms": sorted(lat_vals)[len(lat_vals) // 2] if lat_vals else None,
            }
        )
    return out


def register(app: FastAPI) -> None:
    app.include_router(admin)
    app.include_router(agent_api)
    app.include_router(map_api)
