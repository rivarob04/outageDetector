"""Portable logical backup/restore (works for SQLite and Postgres, and between them).

A backup is a .tar.gz holding one JSON-lines file per table plus a manifest. Restores go into
an empty, migrated database. Raw check results can be left out to keep backups small.
"""

from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select

from pulse import __version__
from pulse.db.store import Store
from pulse.db.tables import metadata

# Restore order respects foreign keys.
ORDER = [
    "settings",
    "users",
    "services",
    "agents",
    "notify_channels",
    "notify_rules",
    "probes",
    "components",
    "incidents",
    "incident_updates",
    "feed_status",
    "service_state",
    "state_transitions",
    "rollups",
    "probe_results",
    "notify_log",
    "chatter",
]
SKIP_ALWAYS = {"agent_nonces"}
BATCH = 2000


def _encode(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


async def backup(store: Store, path: Path, include_raw: bool = True) -> dict[str, int]:
    counts: dict[str, int] = {}
    with tarfile.open(path, "w:gz") as tar:
        async with store.engine.connect() as c:
            for name in ORDER:
                if name in SKIP_ALWAYS or (name == "probe_results" and not include_raw):
                    continue
                table = metadata.tables[name]
                buf = io.BytesIO()
                n = 0
                result = await c.stream(select(table))
                async for row in result:
                    buf.write(json.dumps({k: _encode(v) for k, v in row._mapping.items()}).encode())
                    buf.write(b"\n")
                    n += 1
                counts[name] = n
                info = tarfile.TarInfo(f"{name}.jsonl")
                info.size = buf.tell()
                buf.seek(0)
                tar.addfile(info, buf)
        manifest = json.dumps(
            {
                "pulse_backup": 1,
                "version": __version__,
                "created_at": datetime.now(UTC).isoformat(),
                "tables": counts,
            },
            indent=1,
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    return counts


def _decode(table: Any, row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        col = table.c.get(k)
        if col is None:
            continue  # column removed in a newer schema
        if v is not None and col.type.__class__.__name__ == "UTCDateTime":
            v = datetime.fromisoformat(v)
        out[k] = v
    return out


async def _ensure_empty(c: Any) -> None:
    """Only the catalog rows added at startup (untracked services) may exist already."""
    services = metadata.tables["services"]
    tracked = select(func.count()).select_from(services).where(services.c.tracked.is_(True))
    if (await c.execute(tracked)).scalar_one():
        raise ValueError(
            "target database is not empty (watched services exist); "
            "restore into a fresh data directory"
        )
    for t in ("users", "incidents", "probe_results"):
        if (await c.execute(select(func.count()).select_from(metadata.tables[t]))).scalar_one():
            raise ValueError(
                f"target database is not empty (table {t}); restore into a fresh data directory"
            )


async def restore(store: Store, path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with tarfile.open(path, "r:gz") as tar:
        names = set(tar.getnames())
        if "manifest.json" not in names:
            raise ValueError("not a Pulse backup (manifest.json missing)")
        async with store.engine.begin() as c:
            await _ensure_empty(c)
            await c.execute(metadata.tables["services"].delete())  # catalog rows added at startup
            await c.execute(metadata.tables["settings"].delete())
            for name in ORDER:
                member = f"{name}.jsonl"
                if member not in names:
                    continue
                table = metadata.tables[name]
                f = tar.extractfile(member)
                assert f is not None
                batch: list[dict[str, Any]] = []
                n = 0
                for line in f:
                    batch.append(_decode(table, json.loads(line)))
                    if len(batch) >= BATCH:
                        await c.execute(insert(table), batch)
                        n += len(batch)
                        batch = []
                if batch:
                    await c.execute(insert(table), batch)
                    n += len(batch)
                counts[name] = n
            if c.dialect.name == "postgresql":  # move sequences past restored ids
                from sqlalchemy import text

                for name in ORDER:
                    table = metadata.tables[name]
                    if "id" in table.c and table.c.id.autoincrement is True:
                        # `name` comes from the fixed ORDER list above, never from input.
                        sql = (
                            f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "  # noqa: S608
                            f"COALESCE((SELECT MAX(id) FROM {name}), 1))"
                        )
                        await c.execute(text(sql))
    return counts
