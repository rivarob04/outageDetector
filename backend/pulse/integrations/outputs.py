"""Read-only outputs: Prometheus metrics, Atom feed, iCal, SVG badges, public status page."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from string import Template
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from pulse import __version__
from pulse.api.auth import require_admin, require_view
from pulse.verdict.engine import STATE_RANK

public = APIRouter(tags=["integrations"], dependencies=[Depends(require_view)])
open_router = APIRouter(tags=["integrations"])
admin = APIRouter(
    prefix="/api/v1/status-page", tags=["integrations"], dependencies=[Depends(require_admin)]
)

STATE_NUM = {
    "operational": 0,
    "maintenance": 1,
    "degraded": 2,
    "partial_outage": 3,
    "major_outage": 4,
    "unknown": -1,
}
LABEL = {
    "operational": "operational",
    "degraded": "degraded",
    "partial_outage": "partial outage",
    "major_outage": "major outage",
    "maintenance": "maintenance",
    "unknown": "unknown",
}
BADGE_COLOR = {
    "operational": "#2f9e6e",
    "degraded": "#c48a00",
    "partial_outage": "#d2611c",
    "major_outage": "#d12f45",
    "maintenance": "#2f6fd1",
    "unknown": "#6b7890",
}


def _rt(request: Request) -> Any:
    return request.app.state.rt


def _lbl(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


# ------------------------------------------------------------------ metrics
@open_router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> PlainTextResponse:
    rt = _rt(request)
    s = rt.settings
    if not s.metrics_enabled:
        raise HTTPException(404)
    if s.metrics_token:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        given = given or request.query_params.get("token", "")
        if not hmac.compare_digest(given, s.metrics_token):
            raise HTTPException(401, "metrics token required")
    elif not s.public_view:
        await require_view(request, None)
    mon = rt.monitor
    uptime = await rt.uptime.get(rt)
    out: list[str] = []

    def metric(name: str, help_: str, kind: str = "gauge") -> None:
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {kind}")

    metric("pulse_info", "Pulse build info.")
    out.append(f'pulse_info{{version="{__version__}"}} 1')
    metric("pulse_local_network_offline", "1 when this host's connectivity canaries fail.")
    out.append(f"pulse_local_network_offline {int(mon.canary.offline)}")
    metric(
        "pulse_service_state",
        "Service state: -1 unknown, 0 operational, 1 maintenance, "
        "2 degraded, 3 partial outage, 4 major outage.",
    )
    metric("pulse_service_up", "1 when the service is operational or in maintenance.")
    metric("pulse_service_scope", "1 for the service's current scope label.")
    metric("pulse_service_uptime_ratio_90d", "Share of passing checks over 90 days (0-1).")
    up_lines, scope_lines, ratio_lines, state_lines = [], [], [], []
    for sid, svc in mon.services.items():
        v = mon.verdicts.get(sid)
        state = v.state if v else "unknown"
        scope = v.scope if v else "none"
        labels = f'service="{_lbl(sid)}",name="{_lbl(svc.name)}",category="{svc.category}"'
        state_lines.append(f"pulse_service_state{{{labels}}} {STATE_NUM[state]}")
        up_lines.append(
            f"pulse_service_up{{{labels}}} {int(state in ('operational', 'maintenance'))}"
        )
        scope_lines.append(f'pulse_service_scope{{{labels},scope="{scope}"}} 1')
        days = uptime.get(sid)
        if days:
            ratio_lines.append(
                f"pulse_service_uptime_ratio_90d{{{labels}}} "
                f"{sum(days.values()) / len(days) / 100:.5f}"
            )
    out += state_lines + up_lines + scope_lines + ratio_lines
    metric("pulse_probe_up", "1 when the latest check passed.")
    metric("pulse_probe_latency_ms", "Latency of the latest successful check in milliseconds.")
    metric("pulse_probe_consecutive_failures", "Consecutive failed checks.")
    up, lat, fails = [], [], []
    for sid, specs in mon.specs.items():
        for spec in specs:
            h = mon.history.get(spec.id)
            if not h or not h.items:
                continue
            _ts, ok, latency, *_ = h.items[-1]
            labels = f'service="{_lbl(sid)}",probe="{_lbl(spec.id)}",kind="{spec.kind}"'
            up.append(f"pulse_probe_up{{{labels}}} {int(ok)}")
            if ok and latency is not None:
                lat.append(f"pulse_probe_latency_ms{{{labels}}} {latency}")
            fails.append(f"pulse_probe_consecutive_failures{{{labels}}} {h.consecutive_failures()}")
    out += up + lat + fails
    metric("pulse_active_incidents", "Unresolved provider incidents per service.")
    counts: dict[str, int] = {}
    for inc in await rt.store.active_incidents(list(mon.services)):
        if inc["kind"] == "incident":
            counts[inc["service_id"]] = counts.get(inc["service_id"], 0) + 1
    out += [
        f'pulse_active_incidents{{service="{_lbl(sid)}"}} {counts.get(sid, 0)}'
        for sid in mon.services
    ]
    if mon.agents:
        metric("pulse_agent_fresh", "1 when the vantage agent reported recently.")
        out += [
            f'pulse_agent_fresh{{agent="{_lbl(a["name"])}"}} {int(mon.agent_fresh(a))}'
            for a in mon.agents.values()
            if not a.get("revoked")
        ]
    return PlainTextResponse("\n".join(out) + "\n", media_type="text/plain; version=0.0.4")


# ------------------------------------------------------------------ feeds
@public.get("/feed/incidents.atom", include_in_schema=False)
async def atom(request: Request) -> Response:
    rt = _rt(request)
    rows, _ = await rt.store.list_incidents(limit=50, since=datetime.now(UTC) - timedelta(days=30))
    base = (rt.settings.base_url or str(request.base_url)).rstrip("/")
    updated = max([r["last_seen_at"] for r in rows], default=datetime.now(UTC))
    entries = []
    for r in rows:
        status = "resolved" if r["resolved_at"] else r["status"]
        entries.append(
            "<entry>"
            f"<id>urn:pulse:incident:{r['id']}</id>"
            f"<title>{xml_escape(r['service_name'] + ': ' + r['title'])}</title>"
            f"<updated>{(r['resolved_at'] or r['last_seen_at']).isoformat()}</updated>"
            f"<published>{(r['started_at'] or r['first_seen_at']).isoformat()}</published>"
            f'<link href="{xml_escape(r["url"] or f"{base}/service/{r['service_id']}")}"/>'
            f'<category term="{xml_escape(r["kind"])}"/>'
            f"<summary>{xml_escape(f'{r["kind"].title()} ({r["impact"]}), {status}')}</summary>"
            "</entry>"
        )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Pulse incidents</title>"
        f"<id>urn:pulse:{xml_escape(base)}</id>"
        f'<link rel="self" href="{xml_escape(base)}/feed/incidents.atom"/>'
        f"<updated>{updated.isoformat()}</updated>"
        f'<generator version="{__version__}">Pulse</generator>' + "".join(entries) + "</feed>"
    )
    return Response(xml, media_type="application/atom+xml")


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", "\\,").replace("\n", "\\n")


@public.get("/feed/maintenance.ics", include_in_schema=False)
async def ical(request: Request) -> Response:
    rt = _rt(request)
    rows, _ = await rt.store.list_incidents(
        kind="maintenance", limit=200, since=datetime.now(UTC) - timedelta(days=14)
    )
    fmt = "%Y%m%dT%H%M%SZ"
    now = datetime.now(UTC).strftime(fmt)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Pulse//Maintenance//EN",
        "X-WR-CALNAME:Pulse scheduled maintenance",
        "CALSCALE:GREGORIAN",
    ]
    for r in rows:
        start = r["scheduled_for"] or r["started_at"]
        if start is None:
            continue
        end = r["scheduled_until"] or start + timedelta(hours=1)
        lines += [
            "BEGIN:VEVENT",
            f"UID:pulse-{r['id']}@pulse",
            f"DTSTAMP:{now}",
            f"DTSTART:{start.astimezone(UTC).strftime(fmt)}",
            f"DTEND:{end.astimezone(UTC).strftime(fmt)}",
            f"SUMMARY:{_ics_escape(r['service_name'] + ': ' + r['title'])}",
            *([f"URL:{r['url']}"] if r["url"] else []),
            f"STATUS:{'CANCELLED' if r['status'] == 'cancelled' else 'CONFIRMED'}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar")


# ------------------------------------------------------------------ badges
def _text_width(s: str) -> int:
    return int(sum(7 if c.isupper() else 6 for c in s) + 10)


def badge_svg(label: str, message: str, color: str) -> str:
    lw, mw = _text_width(label), _text_width(message)
    w = lw + mw
    label, message = escape(label), escape(message)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" '
        f'aria-label="{label}: {message}"><title>{label}: {message}</title>'
        f'<rect width="{lw}" height="20" fill="#34404f"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{color}"/>'
        '<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw / 2}" y="14" text-anchor="middle">{label}</text>'
        f'<text x="{lw + mw / 2}" y="14" text-anchor="middle">{message}</text></g></svg>'
    )


@public.get("/badge/{service_id}.svg", include_in_schema=False)
async def badge(service_id: str, request: Request) -> Response:
    rt = _rt(request)
    mon = rt.monitor
    if service_id not in mon.services:
        raise HTTPException(404, "service not watched")
    v = mon.verdicts.get(service_id)
    state = v.state if v else "unknown"
    svg = badge_svg(mon.services[service_id].name, LABEL[state], BADGE_COLOR[state])
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=60"})


# ------------------------------------------------------------------ public status page
class StatusPageConfig(BaseModel):
    enabled: bool = False
    title: str = Field(default="Service status", max_length=100)
    intro: str = Field(default="", max_length=500)
    services: list[str] = Field(default_factory=list)


async def _page_cfg(rt: Any) -> StatusPageConfig:
    return StatusPageConfig.model_validate(await rt.store.get_setting("status_page", {}) or {})


@admin.get("")
async def get_status_page(request: Request) -> dict[str, Any]:
    return (await _page_cfg(_rt(request))).model_dump()


@admin.put("")
async def put_status_page(body: StatusPageConfig, request: Request) -> dict[str, bool]:
    rt = _rt(request)
    unknown = [s for s in body.services if s not in rt.monitor.services]
    if unknown:
        raise HTTPException(422, f"not watched: {unknown}")
    await rt.store.set_setting("status_page", body.model_dump())
    return {"ok": True}


async def _page_data(rt: Any) -> tuple[StatusPageConfig, list[dict[str, Any]]]:
    cfg = await _page_cfg(rt)
    if not cfg.enabled:
        raise HTTPException(404, "the public status page is turned off")
    mon = rt.monitor
    items = []
    for sid in cfg.services:
        svc = mon.services.get(sid)
        if svc is None:
            continue
        v = mon.verdicts.get(sid)
        state = v.state if v else "unknown"
        # Never leak local-only problems on a public page: they're about *this* network.
        if v and v.scope in ("local_only", "local_network"):
            state = "unknown" if v.scope == "local_network" else "operational"
        items.append(
            {
                "id": sid,
                "name": svc.name,
                "state": state,
                "since": v.since.isoformat() if v and v.since else None,
            }
        )
    return cfg, items


@open_router.get("/status.json", include_in_schema=False)
async def status_json(request: Request) -> JSONResponse:
    cfg, items = await _page_data(_rt(request))
    worst = max((i["state"] for i in items), key=lambda s: STATE_RANK.get(s, -1), default="unknown")
    return JSONResponse(
        {
            "title": cfg.title,
            "overall": worst,
            "services": items,
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )


@open_router.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_page(request: Request) -> HTMLResponse:
    cfg, items = await _page_data(_rt(request))
    problems = [i for i in items if i["state"] in ("degraded", "partial_outage", "major_outage")]
    headline = (
        "Everything is running normally."
        if not problems
        else (
            f"{problems[0]['name']} is having problems."
            if len(problems) == 1
            else f"{len(problems)} services are having problems."
        )
    )
    shape = {
        "operational": "●",
        "degraded": "▲",
        "partial_outage": "◆",
        "major_outage": "■",
        "maintenance": "◐",
        "unknown": "○",
    }
    rows = "".join(
        f'<li class="st-{i["state"]}"><span class="g" aria-hidden="true">{shape[i["state"]]}</span>'
        f'<span class="n">{escape(i["name"])}</span>'
        f'<span class="s">{LABEL[i["state"]].capitalize()}</span></li>'
        for i in items
    )
    html = Template((Path(__file__).parent / "status_page.html").read_text()).substitute(
        title=escape(cfg.title),
        intro=escape(cfg.intro or headline),
        headline_block=f"<p><strong>{escape(headline)}</strong></p>" if cfg.intro else "",
        rows=rows or "<li>No services selected.</li>",
        updated=f"{datetime.now(UTC):%Y-%m-%d %H:%M}",
    )
    return HTMLResponse(html)


def register(app: FastAPI) -> None:
    app.include_router(public)
    app.include_router(open_router)
    app.include_router(admin)
