"""Agent request signing (HMAC-SHA256 with timestamp + nonce). Shared by hub and agent."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

MAX_SKEW_S = 300
H_AGENT = "x-pulse-agent"
H_TS = "x-pulse-timestamp"
H_NONCE = "x-pulse-nonce"
H_SIG = "x-pulse-signature"


def derive_secret(secret_key: str, agent_id: str) -> str:
    """Per-agent secret derived from the hub key: nothing secret is stored per agent."""
    return hmac.new(secret_key.encode(), f"agent:{agent_id}".encode(), hashlib.sha256).hexdigest()


def make_token(secret_key: str, agent_id: str) -> str:
    return f"{agent_id}.{derive_secret(secret_key, agent_id)}"


def parse_token(token: str) -> tuple[str, str]:
    agent_id, _, secret = token.strip().partition(".")
    if not agent_id or len(secret) != 64:
        raise ValueError("malformed agent token (expected '<agent id>.<64 hex chars>')")
    return agent_id, secret


def canonical(ts: str, nonce: str, method: str, path: str, body: bytes) -> bytes:
    return "\n".join([ts, nonce, method.upper(), path, hashlib.sha256(body).hexdigest()]).encode()


def sign(secret: str, ts: str, nonce: str, method: str, path: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), canonical(ts, nonce, method, path, body), hashlib.sha256
    ).hexdigest()


def signed_headers(token: str, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    agent_id, secret = parse_token(token)
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return {
        H_AGENT: agent_id,
        H_TS: ts,
        H_NONCE: nonce,
        H_SIG: sign(secret, ts, nonce, method, path, body),
    }


@dataclass
class Verified:
    agent_id: str
    nonce: str


class SignatureError(Exception):
    pass


def verify(
    secret_key: str,
    headers: dict[str, str],
    method: str,
    path: str,
    body: bytes,
    now: float | None = None,
) -> Verified:
    """Check signature and clock skew. Nonce replay is checked by the caller (needs storage)."""
    agent_id = headers.get(H_AGENT, "")
    ts = headers.get(H_TS, "")
    nonce = headers.get(H_NONCE, "")
    sig = headers.get(H_SIG, "")
    if not (agent_id and ts.isdigit() and 16 <= len(nonce) <= 64 and sig):
        raise SignatureError("missing signature headers")
    now = time.time() if now is None else now
    if abs(now - int(ts)) > MAX_SKEW_S:
        raise SignatureError("timestamp outside the allowed window (check the agent's clock)")
    expected = sign(derive_secret(secret_key, agent_id), ts, nonce, method, path, body)
    if not hmac.compare_digest(expected, sig):
        raise SignatureError("bad signature")
    return Verified(agent_id, nonce)
