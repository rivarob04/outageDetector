"""Table-driven tests for the verdict engine. Every rule in docs/verdict.md is covered."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from pulse.verdict.engine import (
    AgentSignal,
    FeedSignal,
    PrevState,
    ProbeSignal,
    VerdictConfig,
    VerdictInputs,
    evaluate,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
CFG = VerdictConfig()


def ok(kind: str = "http", n: int = 0) -> ProbeSignal:
    return ProbeSignal(f"svc:{kind}:{n}", kind, consecutive_failures=0, last_ok=True)


def fail(kind: str = "http", n: int = 0, times: int = 2, error: str = "timeout") -> ProbeSignal:
    return ProbeSignal(
        f"svc:{kind}:{n}", kind, consecutive_failures=times, last_ok=False, error=error
    )


def slow(ratio: float = 4.2, n: int = 0) -> ProbeSignal:
    return ProbeSignal(f"svc:http:{n}", "http", last_ok=True, degraded=True, latency_ratio=ratio)


def agent(
    name: str, failing: bool = False, degraded: bool = False, fresh: bool = True
) -> AgentSignal:
    return AgentSignal(name, name, fresh=fresh, failing=failing, degraded=degraded)


FEED_OK = FeedSignal(fresh=True, indicator="none")
FEED_STALE = FeedSignal(fresh=False, indicator="critical", incident_titles=("old",))


def feed(indicator: str, *titles: str) -> FeedSignal:
    if indicator == "maintenance":
        return FeedSignal(True, "maintenance", maintenance_titles=titles)  # type: ignore[arg-type]
    return FeedSignal(True, indicator, incident_titles=titles)  # type: ignore[arg-type]


@dataclass
class Case:
    name: str
    inputs: VerdictInputs
    state: str
    scope: str
    reason_contains: str | None = None


CASES = [
    # --- local network (rules 1-2) ---
    Case(
        "offline, no feed",
        VerdictInputs(probes=(fail(),), local_network=True),
        "unknown",
        "local_network",
        "offline",
    ),
    Case(
        "offline, feed operational",
        VerdictInputs(feed=FEED_OK, probes=(fail(),), local_network=True),
        "unknown",
        "local_network",
    ),
    Case(
        "offline, fresh feed incident still shown",
        VerdictInputs(feed=feed("major", "API errors"), local_network=True),
        "partial_outage",
        "provider_confirmed",
        "API errors",
    ),
    Case(
        "offline, stale feed ignored",
        VerdictInputs(feed=FEED_STALE, local_network=True),
        "unknown",
        "local_network",
    ),
    # --- provider confirmed (rule 3) ---
    Case(
        "feed minor -> degraded",
        VerdictInputs(feed=feed("minor", "Slow builds"), probes=(ok(),)),
        "degraded",
        "provider_confirmed",
        "Provider confirmed: Slow builds",
    ),
    Case(
        "feed major -> partial",
        VerdictInputs(feed=feed("major", "x")),
        "partial_outage",
        "provider_confirmed",
    ),
    Case(
        "feed critical -> major",
        VerdictInputs(feed=feed("critical", "x")),
        "major_outage",
        "provider_confirmed",
    ),
    Case(
        "provider down but probes pass: trust provider",
        VerdictInputs(feed=feed("critical", "Outage"), probes=(ok(), ok("dns", 1))),
        "major_outage",
        "provider_confirmed",
    ),
    Case(
        "maintenance",
        VerdictInputs(feed=feed("maintenance", "DB upgrade"), probes=(ok(),)),
        "maintenance",
        "provider_confirmed",
        "maintenance in progress: DB upgrade",
    ),
    Case(
        "feed minor escalated by widespread failure",
        VerdictInputs(feed=feed("minor", "x"), probes=(fail(),), agents=(agent("a", True),)),
        "major_outage",
        "provider_confirmed",
        "worse than the provider reports",
    ),
    Case(
        "feed minor with local-only failure keeps feed state",
        VerdictInputs(feed=feed("minor", "x"), probes=(fail(),), agents=(agent("a"),)),
        "degraded",
        "provider_confirmed",
        "failing",
    ),
    Case(
        "multiple incidents summarised",
        VerdictInputs(feed=feed("major", "A", "B", "C")),
        "partial_outage",
        "provider_confirmed",
        "(+2 more)",
    ),
    # --- widespread (rule 4) ---
    Case(
        "local + 1 agent fail -> widespread major",
        VerdictInputs(feed=FEED_OK, probes=(fail(),), agents=(agent("vps", True),)),
        "major_outage",
        "widespread",
        "hasn't posted an incident",
    ),
    Case(
        "2 of 3 points fail -> widespread partial",
        VerdictInputs(probes=(fail(),), agents=(agent("a", True), agent("b"))),
        "partial_outage",
        "widespread",
        "2 of 3",
    ),
    Case(
        "agents only fail (2 of 3) -> widespread",
        VerdictInputs(probes=(ok(),), agents=(agent("a", True), agent("b", True))),
        "partial_outage",
        "widespread",
        "Agents failing: a, b",
    ),
    Case(
        "widespread without feed mentions it",
        VerdictInputs(probes=(fail(),), agents=(agent("a", True),)),
        "major_outage",
        "widespread",
        "No official status feed",
    ),
    Case(
        "1 of 4 failing is not widespread",
        VerdictInputs(probes=(ok(),), agents=(agent("a", True), agent("b"), agent("c"))),
        "operational",
        "none",
        "1 vantage agent report",
    ),
    Case(
        "stale agents don't count",
        VerdictInputs(probes=(fail(),), agents=(agent("a", True, fresh=False),)),
        "major_outage",
        "local_only",
        "no vantage agents",
    ),
    # --- local only (rule 5) ---
    Case(
        "all local probes fail, no agents",
        VerdictInputs(feed=FEED_OK, probes=(fail(), fail("dns", 1))),
        "major_outage",
        "local_only",
        "The provider reports no incident",
    ),
    Case(
        "some local probes fail -> partial",
        VerdictInputs(probes=(fail(), ok("dns", 1))),
        "partial_outage",
        "local_only",
        "1 of 2 checks",
    ),
    Case(
        "local fail, agents fine",
        VerdictInputs(probes=(fail(),), agents=(agent("a"), agent("b"))),
        "major_outage",
        "local_only",
        "2 vantage agents can reach it",
    ),
    Case(
        "single failure is below threshold",
        VerdictInputs(probes=(fail(times=1),)),
        "operational",
        "none",
    ),
    Case(
        "dns fails locally but public resolvers ok",
        VerdictInputs(
            probes=(ProbeSignal("s:dns:0", "dns", 3, False, error="dns", public_dns_ok=True),),
            agents=(agent("a", True),),
        ),
        "major_outage",
        "local_only",
        "your DNS",
    ),
    Case(
        "error type in reason",
        VerdictInputs(probes=(fail(error="tls"),)),
        "major_outage",
        "local_only",
        "HTTP (tls)",
    ),
    # --- latency (rule 6) ---
    Case(
        "slow locally",
        VerdictInputs(feed=FEED_OK, probes=(slow(4.2),)),
        "degraded",
        "local_only",
        "4.2× its normal level",
    ),
    Case(
        "slow locally and at an agent",
        VerdictInputs(probes=(slow(),), agents=(agent("a", degraded=True),)),
        "degraded",
        "widespread",
        "Slow from agents: a",
    ),
    # --- operational / unknown (rules 7-8) ---
    Case(
        "all good",
        VerdictInputs(feed=FEED_OK, probes=(ok(), ok("dns", 1))),
        "operational",
        "none",
        "2 checks passing",
    ),
    Case(
        "feed only, operational",
        VerdictInputs(feed=FEED_OK),
        "operational",
        "none",
        "all systems operational",
    ),
    Case(
        "stale feed is noted",
        VerdictInputs(feed=FEED_STALE, probes=(ok(),)),
        "operational",
        "none",
        "couldn't be fetched",
    ),
    Case("no data", VerdictInputs(), "unknown", "none", "No data"),
    Case(
        "probes never ran",
        VerdictInputs(probes=(ProbeSignal("s:http:0", "http"),)),
        "unknown",
        "none",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_decision_table(case: Case) -> None:
    v = evaluate(case.inputs, None, CFG, NOW)
    assert (v.state, v.scope) == (case.state, case.scope), v.reasons
    if case.reason_contains:
        assert any(case.reason_contains in r for r in v.reasons), v.reasons
    assert v.changed and v.since == NOW


# --- flap protection ---------------------------------------------------------


def prev(state: str, scope: str, **kw: object) -> PrevState:
    return PrevState(state, scope, NOW - timedelta(hours=1), ("old reason",), **kw)  # type: ignore[arg-type]


def test_change_needs_confirmation() -> None:
    p = prev("operational", "none")
    inp = VerdictInputs(probes=(fail(),))
    v1 = evaluate(inp, p, CFG, NOW)
    assert (v1.state, v1.pending_state, v1.pending_count) == ("operational", "major_outage", 1)
    assert any("Confirming change to major outage (1/2)" in r for r in v1.reasons)
    p2 = PrevState(
        v1.state,
        v1.scope,
        v1.since,
        v1.reasons,
        v1.pending_state,
        v1.pending_scope,
        v1.pending_count,
    )
    v2 = evaluate(inp, p2, CFG, NOW + timedelta(minutes=1))
    assert v2.state == "major_outage" and v2.changed
    assert v2.since == NOW + timedelta(minutes=1)


def test_confirming_reason_does_not_accumulate() -> None:
    p = prev(
        "operational",
        "none",
        pending_state="major_outage",
        pending_scope="local_only",
        pending_count=0,
    )
    cfg = VerdictConfig(change_threshold=5)
    v = evaluate(VerdictInputs(probes=(fail(),)), p, cfg, NOW)
    p2 = PrevState(
        v.state, v.scope, v.since, v.reasons, v.pending_state, v.pending_scope, v.pending_count
    )
    v2 = evaluate(VerdictInputs(probes=(fail(),)), p2, cfg, NOW)
    assert sum(r.startswith("Confirming") for r in v2.reasons) == 1


def test_recovery_needs_three() -> None:
    p = prev("major_outage", "local_only")
    inp = VerdictInputs(probes=(ok(),))
    state = p
    for i in range(1, 3):
        v = evaluate(inp, state, CFG, NOW)
        assert v.state == "major_outage" and v.pending_count == i
        state = PrevState(
            v.state, v.scope, v.since, v.reasons, v.pending_state, v.pending_scope, v.pending_count
        )
    v = evaluate(inp, state, CFG, NOW)
    assert v.state == "operational"


def test_flapping_resets_pending() -> None:
    p = prev(
        "operational",
        "none",
        pending_state="major_outage",
        pending_scope="local_only",
        pending_count=1,
    )
    v = evaluate(VerdictInputs(probes=(slow(),)), p, CFG, NOW)
    assert v.state == "operational" and v.pending_state == "degraded" and v.pending_count == 1


def test_provider_confirmed_is_immediate() -> None:
    v = evaluate(VerdictInputs(feed=feed("critical", "x")), prev("operational", "none"), CFG, NOW)
    assert v.state == "major_outage" and v.changed


def test_local_network_is_immediate() -> None:
    v = evaluate(VerdictInputs(local_network=True), prev("operational", "none"), CFG, NOW)
    assert v.state == "unknown" and v.scope == "local_network"


def test_scope_refinement_is_immediate() -> None:
    inp = VerdictInputs(probes=(fail(),), agents=(agent("a", True),))
    v = evaluate(inp, prev("major_outage", "local_only"), CFG, NOW)
    assert (v.state, v.scope) == ("major_outage", "widespread") and v.changed


def test_leaving_unknown_is_immediate() -> None:
    v = evaluate(VerdictInputs(probes=(ok(),)), prev("unknown", "local_network"), CFG, NOW)
    assert v.state == "operational"


def test_unchanged_keeps_since() -> None:
    p = prev("operational", "none")
    v = evaluate(VerdictInputs(probes=(ok(),)), p, CFG, NOW)
    assert not v.changed and v.since == p.since


def test_advisories_pass_through_without_changing_state() -> None:
    inp = VerdictInputs(probes=(ok(),), advisories=("Possibly affected via AWS us-east-1",))
    v = evaluate(inp, None, CFG, NOW)
    assert v.state == "operational" and v.advisories == inp.advisories
