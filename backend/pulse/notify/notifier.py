"""The notifier: reacts to state/connectivity events and applies alert rules."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pulse.events import Event
from pulse.notify.deliver import DeliveryError, deliver
from pulse.notify.rules import LABEL, SHAPE, Rule, decide, format_state, in_quiet_hours
from pulse.verdict.engine import STATE_RANK

log = logging.getLogger("pulse.notify")

DIGEST_HOUR = 8


class Notifier:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.last_sent: dict[tuple[int, str], datetime] = {}
        self.alerted: set[tuple[int, str]] = set()  # (rule id, service) with an open problem alert
        self.offline_since: datetime | None = None

    async def handle(self, ev: Event) -> None:
        if ev.type == "state":
            await self.on_state(ev.data, ev.ts)
        elif ev.type == "connectivity":
            await self.on_connectivity(ev.data, ev.ts)

    async def _rules(self) -> tuple[list[Rule], dict[int, dict[str, Any]]]:
        channels = {c["id"]: c for c in await self.rt.store.list_channels() if c["enabled"]}
        rules = [Rule.from_row(r) for r in await self.rt.store.list_rules()]
        return [r for r in rules if r.enabled and r.channel_id in channels], channels

    async def on_state(self, data: dict[str, Any], now: datetime) -> None:
        if self.rt.monitor.canary.offline or data["scope"] == "local_network":
            return  # paused while we're offline; a summary is sent on recovery
        rules, channels = await self._rules()
        base = self.rt.settings.base_url
        for rule in rules:
            kind = decide(rule, data)
            if kind is None:
                continue
            key = (rule.id, data["service_id"])
            if kind == "recovery" and key not in self.alerted:
                continue  # never announce recovery of something we never alerted
            if in_quiet_hours(rule, now):
                await self.rt.store.log_notification(
                    rule.channel_id,
                    data["service_id"],
                    kind,
                    "suppressed (quiet hours)",
                    True,
                    None,
                )
                continue
            last = self.last_sent.get(key)
            if kind == "problem" and last and (now - last).total_seconds() < rule.cooldown_s:
                continue
            title, body = format_state(data, base)
            ok = await self.send(
                channels[rule.channel_id],
                title,
                body,
                {"event": kind, **data},
                data["service_id"],
                kind,
            )
            if ok:
                self.last_sent[key] = now
                if kind == "problem":
                    self.alerted.add(key)
                else:
                    self.alerted.discard(key)

    async def on_connectivity(self, data: dict[str, Any], now: datetime) -> None:
        if data.get("offline"):
            self.offline_since = now
            return
        if self.offline_since is None:
            return
        started, self.offline_since = self.offline_since, None
        mins = max(1, round((now - started).total_seconds() / 60))
        title = f"{SHAPE['unknown']} You were offline for {mins} min"
        body = (
            f"This Pulse host lost internet access from {started:%H:%M} to {now:%H:%M} UTC. "
            "Alerts were paused meanwhile; current problems are on the dashboard."
        )
        _rules, channels = await self._rules()
        for ch_id in {r.channel_id for r in _rules}:
            await self.send(
                channels[ch_id],
                title,
                body,
                {
                    "event": "offline_summary",
                    "offline_from": started.isoformat(),
                    "offline_to": now.isoformat(),
                },
                None,
                "offline",
            )

    async def send(
        self,
        channel: dict[str, Any],
        title: str,
        body: str,
        payload: dict[str, Any],
        service_id: str | None,
        kind: str,
    ) -> bool:
        try:
            await deliver(channel, title, body, payload)
            await self.rt.store.log_notification(channel["id"], service_id, kind, title, True, None)
            return True
        except (DeliveryError, Exception) as exc:
            log.warning("notification via %s failed: %s", channel["name"], exc)
            await self.rt.store.log_notification(
                channel["id"], service_id, kind, title, False, str(exc)
            )
            return False

    # ------------------------------------------------------------------ digest
    async def digest_job(self) -> None:
        now = datetime.now(UTC)
        rules, channels = await self._rules()
        for rule in [r for r in rules if r.digest]:
            local = now.astimezone(_tz(rule.tz))
            key = f"digest_sent:{rule.id}"
            if (
                local.hour < DIGEST_HOUR
                or await self.rt.store.get_setting(key) == local.date().isoformat()
            ):
                continue
            title, body = await self.build_digest(rule, now)
            if await self.send(
                channels[rule.channel_id], title, body, {"event": "digest"}, None, "digest"
            ):
                await self.rt.store.set_setting(key, local.date().isoformat())

    async def build_digest(self, rule: Rule, now: datetime) -> tuple[str, str]:
        mon = self.rt.monitor
        problems = [
            (sid, v)
            for sid, v in mon.verdicts.items()
            if sid in mon.services
            and STATE_RANK.get(v.state, 0) >= STATE_RANK["degraded"]
            and (not rule.services or sid in rule.services)
            and (not rule.categories or mon.services[sid].category in rule.categories)
        ]
        rows, _ = await self.rt.store.list_incidents(
            kind="incident", limit=50, since=now - timedelta(days=1)
        )
        lines = ["**Right now**"]
        lines += [
            f"- {SHAPE[v.state]} {mon.services[sid].name}: {LABEL[v.state]}" for sid, v in problems
        ] or ["- Everything is up"]
        lines.append("\n**Provider incidents in the last 24 hours**")
        lines += [
            f"- {r['service_name']}: {r['title']}" + (" (resolved)" if r["resolved_at"] else "")
            for r in rows[:15]
        ] or ["- None"]
        n = len(problems)
        title = "Pulse daily digest: " + (
            "all clear" if not n else f"{n} service{'s' if n > 1 else ''} with problems"
        )
        return title, "\n".join(lines)


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")
