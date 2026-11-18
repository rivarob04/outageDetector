"""Authentication: admin sessions, CSRF, trusted proxy headers, login rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, HTTPException, Request, status

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(
        **_SCRYPT, s=base64.b64encode(salt).decode(), h=base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            dklen=32,
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


class LoginLimiter:
    """At most ``max_failures`` failed logins per IP per ``window_s``."""

    def __init__(self, max_failures: int = 5, window_s: int = 300) -> None:
        self.max = max_failures
        self.window = window_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, ip: str) -> bool:
        q = self._fails[ip]
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.max

    def fail(self, ip: str) -> None:
        self._fails[ip].append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _trusted(ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(c, strict=False) for c in cidrs)


async def current_user(request: Request) -> dict[str, Any] | None:
    rt = request.app.state.rt
    s = rt.settings
    if s.trusted_auth_header:
        name = request.headers.get(s.trusted_auth_header)
        # Use the TCP peer, not X-Forwarded-For, to decide whether the header is trustworthy.
        peer = request.scope.get("client")
        peer_ip = peer[0] if peer else ""
        if name and _trusted(peer_ip, s.list_setting(s.trusted_proxies)):
            return {"id": 0, "username": name, "via": "header"}
    uid = request.session.get("uid")
    if uid is None:
        return None
    user = await rt.store.get_user_by_id(int(uid))
    if user is None:
        request.session.clear()
        return None
    return {"id": user["id"], "username": user["username"], "via": "session"}


async def require_view(
    request: Request, user: dict[str, Any] | None = Depends(current_user)
) -> None:
    if user is None and not request.app.state.rt.settings.public_view:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")


async def require_admin(
    request: Request, user: dict[str, Any] | None = Depends(current_user)
) -> dict[str, Any]:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
    if request.method not in SAFE_METHODS and user["via"] == "session":
        token = request.headers.get("x-csrf-token", "")
        expected = request.session.get("csrf", "")
        if not expected or not hmac.compare_digest(token, expected):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")
    return user


def start_session(request: Request, user_id: int) -> str:
    request.session.clear()
    request.session["uid"] = user_id
    request.session["csrf"] = secrets.token_urlsafe(24)
    return str(request.session["csrf"])
