"""Start notification and integration services with the runtime."""

from __future__ import annotations

from typing import Any

from pulse.notify.notifier import Notifier


async def start(rt: Any, background: bool) -> None:
    notifier = Notifier(rt)
    rt.notifier = notifier
    rt.bus.add_handler(notifier.handle)
    if background:
        rt.monitor.sched.add("digest", notifier.digest_job, 300, initial_delay=90)
    if rt.settings.mqtt_url and background:
        from pulse.integrations.mqtt import MqttBridge

        bridge = MqttBridge(rt)
        rt.mqtt = bridge
        rt.bus.add_handler(bridge.on_event)
        rt.extras.append(bridge)
