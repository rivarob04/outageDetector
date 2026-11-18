from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from pulse.app import create_app
from pulse.backup import backup, restore
from pulse.config import Settings
from pulse.probes.types import ProbeResult
from tests.test_api import rt_of, setup_admin


async def test_backup_restore_roundtrip(client: httpx.AsyncClient, tmp_path: Path) -> None:
    csrf = await setup_admin(client)
    h = {"x-csrf-token": csrf}
    await client.post("/api/v1/services/github/track", headers=h)
    await client.post(
        "/api/v1/services",
        headers=h,
        json={"name": "NAS", "probes": [{"kind": "http", "url": "https://nas.example"}]},
    )
    await client.post("/api/v1/alerts/channels", headers=h, json={"name": "x", "url": "ntfy://t"})
    rt = rt_of(client)
    for spec in rt.monitor.specs["github"]:
        await rt.monitor.record_result(
            "github", ProbeResult(spec.id, datetime.now(UTC), True, 50.0)
        )
    await rt.monitor.evaluate("github")
    out = tmp_path / "b.tar.gz"
    counts = await backup(rt.store, out)
    assert counts["probe_results"] == 3 and counts["users"] == 1
    small = await backup(rt.store, tmp_path / "small.tar.gz", include_raw=False)
    assert "probe_results" not in small

    target = Settings(data_dir=tmp_path / "new", secret_key="test-secret", log_json=False)
    app = create_app(target, start_background=False)
    async with app.router.lifespan_context(app):
        store = app.state.rt.store
        with pytest.raises(ValueError, match="not empty"):
            await restore(rt.store, out)
        restored = await restore(store, out)
        assert restored["probe_results"] == 3
        assert {s["id"] for s in await store.list_services(tracked=True)} == {"github", "nas"}
        assert (await store.get_user("admin")) is not None
        assert len(await store.list_channels()) == 1
        assert (await store.get_states())["github"]["state"] == "operational"


async def test_restore_rejects_non_backup(client: httpx.AsyncClient, tmp_path: Path) -> None:
    import tarfile

    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz"):
        pass
    with pytest.raises(ValueError, match="manifest"):
        await restore(rt_of(client).store, bad)
