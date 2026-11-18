"""Alert channels, rules, test sends and the notification log."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from pulse.api.auth import require_admin
from pulse.api.views import iso
from pulse.catalog.schema import CATEGORY_LABELS
from pulse.notify.deliver import DeliveryError, deliver, mask_url
from pulse.notify.rules import ALL_SCOPES, PROBLEM_STATES

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"], dependencies=[Depends(require_admin)])


ScopeName = Literal["provider_confirmed", "widespread", "local_only"]


def _default_scopes() -> list[ScopeName]:
    return ["provider_confirmed", "widespread"]


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["apprise", "webhook"] = "apprise"
    url: str | None = Field(default=None, max_length=2000)  # omitted on update = keep
    enabled: bool = True


class RuleIn(BaseModel):
    channel_id: int
    services: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    min_state: Literal["degraded", "partial_outage", "major_outage"] = "partial_outage"
    scopes: list[ScopeName] = Field(default_factory=_default_scopes)
    quiet_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    tz: str = "UTC"
    cooldown_s: int = Field(default=600, ge=0, le=86400)
    recovery: bool = True
    digest: bool = False
    enabled: bool = True

    @field_validator("tz")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"unknown time zone {v}") from exc
        return v

    @field_validator("categories")
    @classmethod
    def _cats(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in CATEGORY_LABELS]
        if bad:
            raise ValueError(f"unknown categories {bad}")
        return v


def _rt(request: Request) -> Any:
    return request.app.state.rt


def channel_out(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c["id"],
        "name": c["name"],
        "kind": c["kind"],
        "url": mask_url(c["url"]),
        "enabled": c["enabled"],
        "has_secret": bool(c.get("secret")),
        "created_at": iso(c["created_at"]),
    }


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    rt = _rt(request)
    return {
        "channels": [channel_out(c) for c in await rt.store.list_channels()],
        "rules": await rt.store.list_rules(),
        "log": [{**r, "ts": iso(r["ts"])} for r in await rt.store.notification_log(50)],
        "options": {
            "states": list(PROBLEM_STATES),
            "scopes": list(ALL_SCOPES),
            "categories": CATEGORY_LABELS,
        },
    }


@router.post("/channels", status_code=201)
async def create_channel(body: ChannelIn, request: Request) -> dict[str, Any]:
    if not body.url:
        raise HTTPException(422, "url is required")
    if body.kind == "webhook" and not body.url.startswith(("https://", "http://")):
        raise HTTPException(422, "webhook URL must start with http(s)://")
    secret = secrets.token_hex(24) if body.kind == "webhook" else None
    cid = await _rt(request).store.save_channel(
        {
            "name": body.name,
            "kind": body.kind,
            "url": body.url,
            "secret": secret,
            "enabled": body.enabled,
        }
    )
    # Sensible default rule so a new channel works right away.
    await _rt(request).store.save_rule(RuleIn(channel_id=cid).model_dump())
    return {"id": cid, "webhook_secret": secret}


@router.put("/channels/{channel_id}")
async def update_channel(channel_id: int, body: ChannelIn, request: Request) -> dict[str, bool]:
    rt = _rt(request)
    if await rt.store.get_channel(channel_id) is None:
        raise HTTPException(404, "unknown channel")
    values: dict[str, Any] = {"name": body.name, "enabled": body.enabled}
    if body.url:
        values["url"] = body.url
    await rt.store.save_channel(values, channel_id)
    return {"ok": True}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, request: Request) -> dict[str, bool]:
    await _rt(request).store.delete_channel(channel_id)
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    ch = await rt.store.get_channel(channel_id)
    if ch is None:
        raise HTTPException(404, "unknown channel")
    title = "● Pulse test notification"
    body = "If you can read this, alerts from Pulse will reach you here."
    try:
        await deliver(ch, title, body, {"event": "test"})
    except DeliveryError as exc:
        await rt.store.log_notification(channel_id, None, "test", title, False, str(exc))
        raise HTTPException(502, str(exc)) from None
    await rt.store.log_notification(channel_id, None, "test", title, True, None)
    return {"ok": True}


@router.post("/rules", status_code=201)
async def create_rule(body: RuleIn, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    if await rt.store.get_channel(body.channel_id) is None:
        raise HTTPException(422, "unknown channel")
    return {"id": await rt.store.save_rule(body.model_dump())}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, request: Request) -> dict[str, bool]:
    await _rt(request).store.save_rule(body.model_dump(), rule_id)
    return {"ok": True}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, request: Request) -> dict[str, bool]:
    await _rt(request).store.delete_rule(rule_id)
    return {"ok": True}


def register(app: FastAPI) -> None:
    app.include_router(router)
