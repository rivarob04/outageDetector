"""Turn on Phase 8 signals according to feature flags."""

from __future__ import annotations

from typing import Any

from pulse.events import Event
from pulse.signals.anomaly import AnomalyDetector
from pulse.signals.chatter import Chatter
from pulse.signals.correlation import Correlator
from pulse.signals.leadtime import lead_times


async def start(rt: Any, background: bool) -> None:
    s, mon = rt.settings, rt.monitor
    advisors = []
    if s.feature_correlation:
        corr = Correlator(rt)
        advisors.append(corr.advisories)

        async def upstream_changed(ev: Event) -> None:
            if ev.type != "state":
                return
            changed = ev.data["service_id"]
            for sid, svc in mon.services.items():
                if any(d.split(":", 1)[0] == changed for d in svc.depends_on):
                    mon.dirty.add(sid)

        rt.bus.add_handler(upstream_changed)
        # Region problems come from feed snapshots, so re-check dependents after each poll.
        rt.bus.add_handler(_incident_dirty(mon))
    if s.feature_chatter:
        chatter = Chatter(rt)
        rt.chatter = chatter
        advisors.append(chatter.advisories)
        if background:
            mon.sched.add("chatter", chatter.job, 600, initial_delay=120)
    if advisors:
        mon.advisor = lambda sid: [a for fn in advisors for a in fn(sid)]
    if s.feature_anomaly:
        detector = AnomalyDetector(rt)
        rt.anomaly = detector
        mon.anomaly = detector
        if background:
            mon.sched.add("anomaly-baselines", detector.refresh, 1800, initial_delay=45)

    async def lead(days: int) -> dict[str, dict[str, Any]]:
        return await lead_times(rt, days)

    rt.lead_times = lead


def _incident_dirty(mon: Any) -> Any:
    async def handler(ev: Event) -> None:
        if ev.type == "incident":
            mon.dirty.update(sid for sid, svc in mon.services.items() if svc.depends_on)

    return handler
