"""SSRF guard for user-supplied probe targets."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

_BLOCKED_V4 = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local incl. cloud metadata 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
]
_BLOCKED_V6 = [
    ipaddress.ip_network(n)
    for n in ("::1/128", "::/128", "fc00::/7", "fe80::/10", "ff00::/8", "::ffff:0:0/96")
]


class SSRFBlocked(Exception):
    pass


def is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    nets = _BLOCKED_V4 if isinstance(addr, ipaddress.IPv4Address) else _BLOCKED_V6
    return any(addr in n for n in nets)


async def resolve(host: str, port: int = 443) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def check_host(host: str, port: int, allow_private: bool) -> list[str]:
    """Resolve ``host`` and raise :class:`SSRFBlocked` if any address is private."""
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        addrs = await resolve(host, port)
    if not allow_private:
        bad = [a for a in addrs if is_blocked_ip(a)]
        if bad:
            raise SSRFBlocked(f"{host} resolves to a private/reserved address ({bad[0]})")
    return addrs
