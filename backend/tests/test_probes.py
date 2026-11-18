from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from pulse.probes import dns as dns_probe
from pulse.probes.baseline import baseline, consecutive_slow, is_slow, latency_ratio, percentile
from pulse.probes.canary import CanaryState, canary_specs
from pulse.probes.http import make_probe_client, run_http
from pulse.probes.ssrf import SSRFBlocked, check_host, is_blocked_ip
from pulse.probes.tcp import run_tcp
from pulse.probes.types import ProbeResult, ProbeSpec


async def fake_resolver(host: str, port: int, allow_private: bool) -> list[str]:
    return ["93.184.216.34"]


def client_for(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return make_probe_client("pulse-test", transport=httpx.MockTransport(handler))


def spec(**kw: object) -> ProbeSpec:
    base: dict[str, object] = {
        "id": "svc:http:0",
        "service_id": "svc",
        "kind": "http",
        "url": "https://example.com/",
    }
    base.update(kw)
    return ProbeSpec(**base)  # type: ignore[arg-type]


async def test_http_ok() -> None:
    c = client_for(lambda r: httpx.Response(200, text="hello"))
    res = await run_http(spec(), c, resolver=fake_resolver)
    assert res.ok and res.status_code == 200 and res.error is None
    assert res.latency_ms is not None and res.dns_ms is not None


async def test_http_bad_status_and_accept_list() -> None:
    c = client_for(lambda r: httpx.Response(401))
    assert (await run_http(spec(), c, resolver=fake_resolver)).error == "http_status"
    assert (await run_http(spec(accept_status=(401,)), c, resolver=fake_resolver)).ok


async def test_http_keyword_assertion() -> None:
    c = client_for(lambda r: httpx.Response(200, text="all good"))
    assert (await run_http(spec(keyword="good"), c, resolver=fake_resolver)).ok
    res = await run_http(spec(keyword="missing"), c, resolver=fake_resolver)
    assert not res.ok and res.error == "assertion"


async def test_http_follows_redirects_and_records_final_url() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/":
            return httpx.Response(302, headers={"location": "/home"})
        return httpx.Response(200)

    res = await run_http(spec(), client_for(handler), resolver=fake_resolver)
    assert res.ok and res.detail["final_url"] == "https://example.com/home"


async def test_http_redirect_loop() -> None:
    c = client_for(lambda r: httpx.Response(302, headers={"location": "/"}))
    res = await run_http(spec(), c, resolver=fake_resolver)
    assert not res.ok and "redirects" in res.detail["message"]


async def test_http_timeout_and_connect_errors() -> None:
    def timeout(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=r)

    def refused(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=r)

    assert (await run_http(spec(), client_for(timeout), resolver=fake_resolver)).error == "timeout"
    assert (await run_http(spec(), client_for(refused), resolver=fake_resolver)).error == "connect"


async def test_guarded_probe_blocks_private_targets() -> None:
    c = client_for(lambda r: httpx.Response(200))
    res = await run_http(spec(url="http://127.0.0.1:8080/", guarded=True), c)
    assert not res.ok and res.error == "ssrf_blocked"
    allowed = await run_http(
        spec(url="http://127.0.0.1:8080/", guarded=True), c, allow_private=True
    )
    assert allowed.ok


async def test_guarded_probe_blocks_redirect_to_metadata() -> None:
    calls: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    async def resolver(host: str, port: int, allow_private: bool) -> list[str]:
        return await check_host(
            host if host != "example.com" else "93.184.216.34", port, allow_private
        )

    res = await run_http(spec(guarded=True), client_for(handler), resolver=resolver)
    assert res.error == "ssrf_blocked"
    assert calls == ["https://example.com/"]


@pytest.mark.parametrize(
    ("ip", "blocked"),
    [
        ("10.1.2.3", True),
        ("192.168.1.1", True),
        ("172.20.0.1", True),
        ("127.0.0.1", True),
        ("169.254.169.254", True),
        ("100.64.1.1", True),
        ("::1", True),
        ("fd00::1", True),
        ("::ffff:10.0.0.1", True),
        ("1.1.1.1", False),
        ("8.8.8.8", False),
        ("2606:4700::1111", False),
    ],
)
def test_ssrf_ranges(ip: str, blocked: bool) -> None:
    assert is_blocked_ip(ip) is blocked


async def test_check_host_literal_ip() -> None:
    with pytest.raises(SSRFBlocked):
        await check_host("10.0.0.5", 80, allow_private=False)
    assert await check_host("10.0.0.5", 80, allow_private=True) == ["10.0.0.5"]


async def test_tcp_probe_against_local_server() -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        s = ProbeSpec(
            id="x:tcp:0",
            service_id="x",
            kind="tcp",
            host="127.0.0.1",
            port=port,
            guarded=True,
            timeout_s=2,
        )
        assert (await run_tcp(s)).error == "ssrf_blocked"
        res = await run_tcp(s, allow_private=True)
        assert res.ok and res.connect_ms is not None
    closed = ProbeSpec(
        id="x:tcp:1", service_id="x", kind="tcp", host="127.0.0.1", port=port, timeout_s=2
    )
    assert (await run_tcp(closed)).error == "connect"


async def test_dns_probe_checks_public_resolvers_only_on_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[object] = []

    async def fake_lookup(host, resolver):  # type: ignore[no-untyped-def]
        ns = resolver.nameservers[0] if resolver.nameservers else "system"
        calls.append(ns)
        return (ns != "system"), 5.0, "ok"

    monkeypatch.setattr(
        dns_probe,
        "make_resolver",
        lambda ns, t: type("R", (), {"nameservers": [ns] if ns else []})(),
    )
    monkeypatch.setattr(dns_probe, "_lookup", fake_lookup)
    s = ProbeSpec(id="x:dns:0", service_id="x", kind="dns", host="example.com")
    res = await dns_probe.run_dns(s, ["1.1.1.1", "8.8.8.8"])
    assert not res.ok and res.error == "dns" and res.detail["public_ok"] is True
    assert calls == ["system", "1.1.1.1", "8.8.8.8"]


def test_baseline_math() -> None:
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert baseline([100.0] * 5) is None
    b = baseline([100.0 + i for i in range(100)])
    assert b and 149 <= b.p50 <= 150 and b.p95 > 190
    assert is_slow(1000, b, 3.0, 300) and not is_slow(500, b, 3.0, 300)
    assert latency_ratio(450, b) == pytest.approx(3.0, rel=0.02)
    tiny = baseline([5.0] * 30)
    assert not is_slow(50, tiny, 3.0, 300)  # 10× but below the absolute floor
    assert consecutive_slow([100, 1000, 1200], b, 3.0, 300, 2)
    assert not consecutive_slow([1000, 100], b, 3.0, 300, 2)


def test_canary_state_needs_two_bad_rounds() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    st = CanaryState()

    def round_(oks: list[bool]) -> list[ProbeResult]:
        return [ProbeResult(f"canary:{i}", now, ok) for i, ok in enumerate(oks)]

    assert not st.update(round_([False, False, True]), now) and not st.offline
    assert st.update(round_([False, False, False]), now) and st.offline
    assert st.offline_since == now
    assert not st.update(round_([True, False, False]), now)  # still majority failing
    assert st.update(round_([True, True, False]), now) and not st.offline


def test_canary_specs_accept_4xx() -> None:
    specs = canary_specs(["https://1.1.1.1"])
    assert 404 in specs[0].accept_status


def test_probe_spec_roundtrip() -> None:
    s = spec(headers=(("X-A", "1"),), accept_status=(401,))
    assert ProbeSpec.from_dict(s.to_dict()) == s
    r = ProbeResult("p", datetime(2026, 1, 1, tzinfo=UTC), True, 12.5, detail={"a": 1})
    assert ProbeResult.from_dict(r.to_dict()) == r
