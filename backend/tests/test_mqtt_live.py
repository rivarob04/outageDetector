"""Talks to a real MQTT broker. Runs when PULSE_TEST_MQTT_URL is set (CI uses Mosquitto)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulse.app import create_app
from pulse.config import Settings
from pulse.probes.types import ProbeResult

MQTT_URL = os.environ.get("PULSE_TEST_MQTT_URL", "")
pytestmark = pytest.mark.skipif(not MQTT_URL, reason="PULSE_TEST_MQTT_URL not set")


async def test_bridge_publishes_discovery_and_state(tmp_path: Path) -> None:
    import aiomqtt

    from pulse.integrations.mqtt import MqttBridge

    s = Settings(
        data_dir=tmp_path,
        secret_key="k",
        log_json=False,
        mqtt_url=MQTT_URL,
        mqtt_base_topic=f"pulse-test-{os.getpid()}",
    )
    app = create_app(s, start_background=False)
    async with app.router.lifespan_context(app):
        rt = app.state.rt
        await rt.monitor.track("github")
        for spec in rt.monitor.specs["github"]:
            await rt.monitor.record_result(
                "github", ProbeResult(spec.id, datetime.now(UTC), True, 50.0)
            )
        await rt.monitor.evaluate("github")
        host = MQTT_URL.split("://", 1)[1].split(":")[0]
        received: dict[str, str] = {}
        async with aiomqtt.Client(host, 1883) as sub:
            await sub.subscribe(f"{s.mqtt_base_topic}/#")
            await sub.subscribe("homeassistant/sensor/pulse_github_state/config")
            bridge = MqttBridge(rt)
            try:
                async with asyncio.timeout(15):
                    async for msg in sub.messages:
                        received[str(msg.topic)] = msg.payload.decode()  # type: ignore[union-attr]
                        if f"{s.mqtt_base_topic}/github/state" in received and any(
                            t.startswith("homeassistant/") for t in received
                        ):
                            break
            finally:
                await bridge.stop()
        state = json.loads(received[f"{s.mqtt_base_topic}/github/state"])
        assert state["state"] == "operational" and state["problem"] is False
        assert received[f"{s.mqtt_base_topic}/status"] == "online"
