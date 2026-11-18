"""Security headers for every response (CSP, framing, sniffing, referrer)."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pulse.config import Settings


def build_csp(settings: Settings) -> str:
    # A custom map style pulls tiles, glyphs and sprites from its host(s).
    extra = ""
    if settings.map_style_url:
        parts = urlsplit(settings.map_style_url)
        extra = f" {parts.scheme}://{parts.netloc} https:"
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            # React sets style attributes; MapLibre positions markers with inline styles.
            "style-src 'self' 'unsafe-inline'",
            f"img-src 'self' data: blob:{extra}",
            "font-src 'self' data:",
            f"connect-src 'self'{extra}",
            "worker-src 'self' blob:",  # MapLibre runs its tile workers from blob: URLs
            "child-src blob:",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


class SecurityHeaders:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.csp = build_csp(settings)
        self.hsts = settings.cookie_secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}

                def add(k: str, v: str) -> None:
                    if k.encode() not in names:
                        headers.append((k.encode(), v.encode()))

                add("x-content-type-options", "nosniff")
                add("referrer-policy", "strict-origin-when-cross-origin")
                add("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()")
                if path.startswith("/api/docs"):
                    pass  # Swagger UI loads its assets from a CDN; leave its CSP alone
                elif path.startswith("/badge/"):
                    add("content-security-policy", "default-src 'none'; style-src 'unsafe-inline'")
                else:
                    add("content-security-policy", self.csp)
                    add("x-frame-options", "DENY")
                if self.hsts:
                    add("strict-transport-security", "max-age=31536000")
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, wrapped)
