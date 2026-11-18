"""Verdict engine: combine all signals into one state + scope + reasons.

Pure: no I/O, no clock (``now`` is passed in). The rules are documented in
``docs/verdict.md``; keep the two in sync and extend the table-driven tests in
``tests/test_verdict.py`` with every rule change.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

State = Literal[
    "operational", "degraded", "partial_outage", "major_outage", "maintenance", "unknown"
]
Scope = Literal["provider_confirmed", "widespread", "local_only", "local_network", "none"]
Impact = Literal["none", "minor", "major", "critical", "maintenance"]

STATE_RANK: dict[str, int] = {
    "unknown": -1,
    "operational": 0,
    "maintenance": 1,
    "degraded": 2,
    "partial_outage": 3,
    "major_outage": 4,
}
FEED_STATE: dict[str, State] = {
    "none": "operational",
    "minor": "degraded",
    "major": "partial_outage",
    "critical": "major_outage",
    "maintenance": "maintenance",
}


@dataclass(frozen=True)
class FeedSignal:
    fresh: bool
    indicator: Impact = "none"
    incident_titles: tuple[str, ...] = ()
    maintenance_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeSignal:
    probe_id: str
    kind: str
    consecutive_failures: int = 0
    last_ok: bool | None = None
    latency_ratio: float | None = None  # current / baseline p95
    degraded: bool = False
    error: str | None = None
    # For DNS probes: did the public resolvers succeed while the system one failed?
    public_dns_ok: bool | None = None
    # What "normal" means for latency_ratio, e.g. "for this hour" (time-of-day baseline).
    latency_basis: str | None = None


@dataclass(frozen=True)
class AgentSignal:
    agent_id: str
    name: str
    fresh: bool
    failing: bool = False
    degraded: bool = False


@dataclass(frozen=True)
class VerdictInputs:
    feed: FeedSignal | None = None
    probes: tuple[ProbeSignal, ...] = ()
    agents: tuple[AgentSignal, ...] = ()
    local_network: bool = False
    advisories: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrevState:
    state: State
    scope: Scope
    since: datetime
    reasons: tuple[str, ...] = ()
    pending_state: State | None = None
    pending_scope: Scope | None = None
    pending_count: int = 0


@dataclass(frozen=True)
class VerdictConfig:
    fail_threshold: int = 2  # consecutive probe failures before a probe counts as failing
    change_threshold: int = 2  # evaluations a worse/different state must persist
    recover_threshold: int = 3  # evaluations "operational" must persist to recover
    widespread_ratio: float = 0.5  # share of vantage points that must fail


@dataclass(frozen=True)
class Verdict:
    state: State
    scope: Scope
    reasons: tuple[str, ...]
    since: datetime
    advisories: tuple[str, ...] = ()
    pending_state: State | None = None
    pending_scope: Scope | None = None
    pending_count: int = 0
    changed: bool = False


@dataclass
class _Candidate:
    state: State
    scope: Scope
    reasons: list[str] = field(default_factory=list)
    immediate: bool = False


def worse(a: State, b: State) -> State:
    return a if STATE_RANK[a] >= STATE_RANK[b] else b


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _candidate(inp: VerdictInputs, cfg: VerdictConfig) -> _Candidate:
    feed = inp.feed
    feed_fresh = feed is not None and feed.fresh
    feed_state: State | None = FEED_STATE[feed.indicator] if feed and feed_fresh else None

    # 1. Local network down: our own probes prove nothing.
    if inp.local_network:
        if feed_state and feed_state != "operational":
            c = _Candidate(feed_state, "provider_confirmed", immediate=True)
            c.reasons.append(_feed_reason(feed))  # type: ignore[arg-type]
            c.reasons.append("Your network appears to be offline; local checks are paused.")
            return c
        return _Candidate(
            "unknown",
            "local_network",
            [
                "Your network appears to be offline (connectivity canaries are failing), "
                "so this service can't be judged right now."
            ],
            immediate=True,
        )

    # 2. Local probes.
    probes = inp.probes
    failing = [p for p in probes if p.consecutive_failures >= cfg.fail_threshold]
    slow = [p for p in probes if p.degraded and p not in failing]
    local_state: State | None = None
    if probes and any(p.last_ok is not None for p in probes):
        if failing and len(failing) == len(probes):
            local_state = "major_outage"
        elif failing:
            local_state = "partial_outage"
        elif slow:
            local_state = "degraded"
        else:
            local_state = "operational"
    dns_local = [p for p in failing if p.kind == "dns" and p.public_dns_ok is True]

    # 3. Vantage agents (fresh only).
    agents = [a for a in inp.agents if a.fresh]
    a_fail = [a for a in agents if a.failing]
    a_slow = [a for a in agents if a.degraded and not a.failing]
    local_failing = local_state in ("partial_outage", "major_outage")
    points = (1 if local_state is not None else 0) + len(agents)
    failing_points = (1 if local_failing else 0) + len(a_fail)
    widespread = (
        failing_points >= 2
        and points > 0
        and failing_points / points >= cfg.widespread_ratio
        and not dns_local
    )

    # 4. Provider-confirmed incident or maintenance.
    if feed_state and feed_state != "operational":
        c = _Candidate(feed_state, "provider_confirmed", immediate=True)
        c.reasons.append(_feed_reason(feed))  # type: ignore[arg-type]
        if widespread:
            probe_state: State = "major_outage" if failing_points == points else "partial_outage"
            if STATE_RANK[probe_state] > STATE_RANK[feed_state]:
                c.state = probe_state
                c.reasons.append(
                    f"Checks are failing from {failing_points} of {points} vantage points, "
                    "which is worse than the provider reports."
                )
        elif local_failing:
            c.reasons.append(_probe_reason(failing, probes))
        return c

    # 5. Failures without a provider incident.
    if widespread:
        state: State = "major_outage" if failing_points == points else "partial_outage"
        c = _Candidate(state, "widespread")
        c.reasons.append(f"Failing from {failing_points} of {points} vantage points.")
        if local_failing:
            c.reasons.append(_probe_reason(failing, probes))
        if a_fail:
            c.reasons.append("Agents failing: " + ", ".join(a.name for a in a_fail) + ".")
        c.reasons.append(
            "The provider hasn't posted an incident yet."
            if feed_fresh
            else "No official status feed is available to confirm."
        )
        return c

    if local_failing:
        c = _Candidate(local_state, "local_only")  # type: ignore[arg-type]
        c.reasons.append(_probe_reason(failing, probes))
        if dns_local:
            c.reasons.append(
                "DNS lookups fail through your resolver but succeed through public resolvers; "
                "your DNS is the likely cause."
            )
        if agents:
            ok = len(agents) - len(a_fail)
            c.reasons.append(
                f"Failing from your network only; {_plural(ok, 'vantage agent')} can reach it."
            )
        else:
            c.reasons.append(
                "Failing from your network only (no vantage agents to compare against)."
            )
        if feed_fresh:
            c.reasons.append("The provider reports no incident.")
        return c

    # Agent-only failures (single agent) are informational.
    agent_note = (
        f"{_plural(len(a_fail), 'vantage agent')} report failures: "
        + ", ".join(a.name for a in a_fail)
        + "."
        if a_fail
        else None
    )

    # 6. Degraded latency.
    slow_points = (1 if local_state == "degraded" else 0) + len(a_slow)
    if slow_points:
        scope: Scope = "widespread" if slow_points >= 2 else "local_only"
        c = _Candidate("degraded", scope)
        for p in slow:
            if p.latency_ratio:
                basis = f" {p.latency_basis}" if p.latency_basis else ""
                c.reasons.append(
                    f"{p.kind.upper()} check latency is {p.latency_ratio:.1f}× "
                    f"its normal level{basis}."
                )
            else:
                c.reasons.append(f"{p.kind.upper()} check is slower than normal.")
        if a_slow:
            c.reasons.append("Slow from agents: " + ", ".join(a.name for a in a_slow) + ".")
        if agent_note:
            c.reasons.append(agent_note)
        return c

    # 7. All good, or no data.
    if local_state == "operational" or feed_fresh or agents:
        c = _Candidate("operational", "none")
        if feed_fresh:
            c.reasons.append("The provider reports all systems operational.")
        if local_state == "operational":
            c.reasons.append(f"{_plural(len(probes), 'check')} passing from your network.")
        if agent_note:
            c.reasons.append(agent_note)
        if feed is not None and not feed.fresh:
            c.reasons.append("The official status feed couldn't be fetched recently.")
        return c
    return _Candidate("unknown", "none", ["No data yet."], immediate=True)


def _feed_reason(feed: FeedSignal) -> str:
    if feed.indicator == "maintenance":
        titles = feed.maintenance_titles or feed.incident_titles
        return "Scheduled maintenance in progress" + (f": {titles[0]}" if titles else ".")
    titles = feed.incident_titles
    if titles:
        extra = f" (+{len(titles) - 1} more)" if len(titles) > 1 else ""
        return f"Provider confirmed: {titles[0]}{extra}"
    return f"The provider reports a {feed.indicator} incident."


def _probe_reason(failing: list[ProbeSignal], probes: tuple[ProbeSignal, ...]) -> str:
    kinds = ", ".join(
        sorted({f"{p.kind.upper()}{f' ({p.error})' if p.error else ''}" for p in failing})
    )
    return f"{len(failing)} of {_plural(len(probes), 'check')} failing: {kinds}."


def evaluate(
    inp: VerdictInputs,
    prev: PrevState | None,
    cfg: VerdictConfig,
    now: datetime,
) -> Verdict:
    """Compute the next verdict, applying flap protection against ``prev``."""
    cand = _candidate(inp, cfg)
    reasons = tuple(cand.reasons)
    adv = inp.advisories

    def apply() -> Verdict:
        changed = prev is None or (prev.state, prev.scope) != (cand.state, cand.scope)
        return Verdict(
            state=cand.state,
            scope=cand.scope,
            reasons=reasons,
            since=now if changed or prev is None else prev.since,
            advisories=adv,
            changed=changed,
        )

    if prev is None or cand.immediate or prev.state == "unknown":
        return apply()
    if (cand.state, cand.scope) == (prev.state, prev.scope):
        return apply()
    if cand.state == prev.state:  # scope refinement only: take it right away
        return apply()

    threshold = cfg.recover_threshold if cand.state == "operational" else cfg.change_threshold
    if (prev.pending_state, prev.pending_scope) == (cand.state, cand.scope):
        count = prev.pending_count + 1
    else:
        count = 1
    if count >= threshold:
        return apply()
    kept = [r for r in prev.reasons if not r.startswith("Confirming change")]
    note = f"Confirming change to {cand.state.replace('_', ' ')} ({count}/{threshold})."
    held = Verdict(
        state=prev.state,
        scope=prev.scope,
        reasons=(*kept, note),
        since=prev.since,
        advisories=adv,
        pending_state=cand.state,
        pending_scope=cand.scope,
        pending_count=count,
        changed=False,
    )
    return replace(held)
