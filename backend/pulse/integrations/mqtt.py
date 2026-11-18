"""MQTT bridge with Home Assistant discovery: every watched service becomes an HA device."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from pulse import __version__
from pulse.events import Event

log = logging.getLogger("pulse.mqtt")

STATES = ["operational", "maintenance", "degraded", "partial_outage", "major_outage", "unknown"]
SCOPES = ["provider_confirmed", "widespread", "local_only", "local_network", "none"]


def discovery_messages(
    prefix: str, base: str, sid: str, name: str
) -> list[tuple[str, dict[str, Any]]]:
    """(topic, payload) pairs announcing one service's entities to Home Assistant."""
    state_topic = f"{base}/{sid}/state"
    device = {
        "identifiers": [f"pulse_{sid}"],
        "name": f"{name} (Pulse)",
        "manufacturer": "Pulse",
        "model": "Service monitor",
        "sw_version": __version__,
    }
    common = {"state_topic": state_topic, "availability_topic": f"{base}/status", "device": device}
    uid = f"pulse_{sid}"
    return [
        (
            f"{prefix}/sensor/{uid}_state/config",
            {
                **common,
                "name": "Status",
                "unique_id": f"{uid}_state",
                "device_class": "enum",
                "options": STATES,
                "value_template": "{{ value_json.state }}",
                "json_attributes_topic": state_topic,
                "icon": "mdi:list-status",
            },
        ),
        (
            f"{prefix}/sensor/{uid}_scope/config",
            {
                **common,
                "name": "Scope",
                "unique_id": f"{uid}_scope",
                "device_class": "enum",
                "options": SCOPES,
                "value_template": "{{ value_json.scope }}",
                "icon": "mdi:map-marker-radius",
            },
        ),
        (
            f"{prefix}/binary_sensor/{uid}_problem/config",
            {
                **common,
                "name": "Problem",
                "unique_id": f"{uid}_problem",
                "device_class": "problem",
                "value_template": "{{ 'ON' if value_json.problem else 'OFF' }}",
            },
        ),
        (
            f"{prefix}/sensor/{uid}_latency/config",
            {
                **common,
                "name": "Latency",
                "unique_id": f"{uid}_latency",
                "unit_of_measurement": "ms",
                "state_class": "measurement",
                "value_template": "{{ value_json.latency_ms }}",
                "icon": "mdi:timer-outline",
            },
        ),
        (
            f"{prefix}/sensor/{uid}_incidents/config",
            {
                **common,
                "name": "Active incidents",
                "unique_id": f"{uid}_incidents",
                "state_class": "measurement",
                "value_template": "{{ value_json.incidents }}",
                "icon": "mdi:alert-circle-outline",
            },
        ),
    ]


class MqttBridge:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        s = rt.settings
        u = urlsplit(s.mqtt_url)
        self.host = u.hostname or "localhost"
        self.port = u.port or (8883 if u.scheme == "mqtts" else 1883)
        self.tls = u.scheme == "mqtts"
        self.prefix = s.mqtt_discovery_prefix.strip("/")
        self.base = s.mqtt_base_topic.strip("/")
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.connected = False
        self.announced: set[str] = set()
        self.task = asyncio.create_task(self.run())

    async def on_event(self, ev: Event) -> None:
        if ev.type == "state":
            self.queue.put_nowait(ev.data["service_id"])

    def payload(self, sid: str, incidents: int) -> dict[str, Any]:
        mon = self.rt.monitor
        v = mon.verdicts.get(sid)
        spark = [x for x in mon.sparkline(sid, points=1, hours=1) if x is not None]
        state = v.state if v else "unknown"
        return {
            "state": state,
            "scope": v.scope if v else "none",
            "problem": state in ("degraded", "partial_outage", "major_outage"),
            "latency_ms": spark[0] if spark else None,
            "incidents": incidents,
            "reasons": list(v.reasons) if v else [],
            "since": v.since.isoformat() if v and v.since else None,
        }

    async def publish_all(self, client: Any, only: set[str] | None = None) -> None:
        mon = self.rt.monitor
        counts: dict[str, int] = {}
        for inc in await self.rt.store.active_incidents(list(mon.services)):
            if inc["kind"] == "incident":
                counts[inc["service_id"]] = counts.get(inc["service_id"], 0) + 1
        for sid, svc in list(mon.services.items()):
            if only is not None and sid not in only:
                continue
            if sid not in self.announced:
                for topic, cfg in discovery_messages(self.prefix, self.base, sid, svc.name):
                    await client.publish(topic, json.dumps(cfg), retain=True)
                self.announced.add(sid)
            await client.publish(
                f"{self.base}/{sid}/state",
                json.dumps(self.payload(sid, counts.get(sid, 0))),
                retain=True,
            )

    async def run(self) -> None:
        import aiomqtt

        s = self.rt.settings
        backoff = 2.0
        while True:
            try:
                async with aiomqtt.Client(
                    self.host,
                    self.port,
                    username=s.mqtt_username or None,
                    password=s.mqtt_password or None,
                    tls_params=aiomqtt.TLSParameters() if self.tls else None,
                    will=aiomqtt.Will(f"{self.base}/status", "offline", retain=True),
                    identifier=f"pulse-{__version__}",
                ) as client:
                    self.connected, backoff, self.announced = True, 2.0, set()
                    log.info("connected to MQTT %s:%s", self.host, self.port)
                    await client.publish(f"{self.base}/status", "online", retain=True)
                    await self.publish_all(client)
                    while True:
                        try:
                            sid = await asyncio.wait_for(self.queue.get(), timeout=60)
                            await self.publish_all(client, {sid})
                        except TimeoutError:
                            await self.publish_all(client)  # refresh latency every minute
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                log.warning("MQTT connection failed (%s); retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def stop(self) -> None:
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
