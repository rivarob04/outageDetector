"""Deliver notifications via Apprise (80+ services) or signed generic webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx


class DeliveryError(Exception):
    pass


def mask_url(url: str) -> str:
    """Hide tokens in channel URLs shown in the UI: keep scheme and host-ish prefix."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return "***"
    head = rest.split("/", 1)[0].split("@")[-1]
    return f"{scheme}://{head[:24]}{'/…' if len(rest) > len(head) else ''}"


async def send_apprise(url: str, title: str, body: str) -> None:
    import apprise

    ap = apprise.Apprise()
    if not ap.add(url):
        raise DeliveryError(
            "Apprise doesn't recognise this URL; see https://github.com/caronc/apprise/wiki"
        )
    ok = await ap.async_notify(body=body, title=title, body_format=apprise.NotifyFormat.MARKDOWN)
    if not ok:
        raise DeliveryError("Apprise reported a delivery failure (check the URL and credentials)")


def webhook_signature(secret: str, ts: str, body: bytes) -> str:
    return (
        "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    )


async def send_webhook(
    url: str, secret: str | None, payload: dict[str, Any], client: httpx.AsyncClient | None = None
) -> None:
    body = json.dumps(payload, default=str).encode()
    ts = str(int(time.time()))
    headers = {
        "content-type": "application/json",
        "x-pulse-timestamp": ts,
        "user-agent": "pulse-webhook/1",
    }
    if secret:
        headers["x-pulse-signature"] = webhook_signature(secret, ts, body)
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        r = await client.post(url, content=body, headers=headers)
        if r.status_code >= 300:
            raise DeliveryError(f"webhook returned HTTP {r.status_code}")
    except httpx.HTTPError as exc:
        raise DeliveryError(f"webhook failed: {exc}") from exc
    finally:
        if own:
            await client.aclose()


async def deliver(
    channel: dict[str, Any],
    title: str,
    body: str,
    payload: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> None:
    if channel["kind"] == "webhook":
        await send_webhook(
            channel["url"], channel.get("secret"), {**payload, "title": title, "text": body}, client
        )
    else:
        await send_apprise(channel["url"], title, body)
