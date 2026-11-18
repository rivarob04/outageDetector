"""Async data-access layer. All SQL lives here (portable SQLite + Postgres)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Table, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from pulse.adapters.base import FeedSnapshot
from pulse.db.tables import (
    agent_nonces_t,
    agents_t,
    chatter_t,
    components_t,
    feed_status_t,
    incident_updates_t,
    incidents_t,
    notify_channels_t,
    notify_log_t,
    notify_rules_t,
    probe_results_t,
    probes_t,
    rollups_t,
    service_state_t,
    services_t,
    settings_t,
    state_transitions_t,
    users_t,
)
from pulse.probes.baseline import percentile
from pulse.probes.types import ProbeResult, ProbeSpec


def utcnow() -> datetime:
    return datetime.now(UTC)


def floor_time(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, UTC)


async def upsert(
    conn: AsyncConnection,
    table: Table,
    rows: list[dict[str, Any]] | dict[str, Any],
    keys: Sequence[str],
    update_cols: Sequence[str] | None = None,
) -> None:
    rows = [rows] if isinstance(rows, dict) else rows
    if not rows:
        return
    ins_fn = pg_insert if conn.dialect.name == "postgresql" else sqlite_insert
    stmt = ins_fn(table).values(rows)
    cols = list(update_cols) if update_cols is not None else [k for k in rows[0] if k not in keys]
    if cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=list(keys), set_={c: stmt.excluded[c] for c in cols}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(keys))
    await conn.execute(stmt)


@dataclass
class FeedChange:
    """What changed when a feed snapshot was saved (drives events and alerts)."""

    new_incidents: list[dict[str, Any]]
    new_updates: list[dict[str, Any]]
    resolved: list[dict[str, Any]]


class Store:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    # -- settings ----------------------------------------------------------------
    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.engine.connect() as c:
            row = (
                await c.execute(select(settings_t.c.value).where(settings_t.c.key == key))
            ).first()
        return default if row is None else row[0]

    async def set_setting(self, key: str, value: Any) -> None:
        async with self.engine.begin() as c:
            await upsert(
                c, settings_t, {"key": key, "value": value, "updated_at": utcnow()}, ["key"]
            )

    # -- services ----------------------------------------------------------------
    async def sync_catalog(self, services: Iterable[tuple[str, str, str]]) -> None:
        """Insert catalog services (id, name, category) that aren't in the DB yet."""
        now = utcnow()
        rows = [
            {
                "id": i,
                "name": n,
                "category": c,
                "source": "catalog",
                "tracked": False,
                "config": None,
                "created_at": now,
                "tracked_at": None,
            }
            for i, n, c in services
        ]
        async with self.engine.begin() as c:
            for chunk in _chunks(rows, 200):
                await upsert(c, services_t, chunk, ["id"], update_cols=["name", "category"])

    async def list_services(self, tracked: bool | None = None) -> list[dict[str, Any]]:
        q = select(services_t).order_by(services_t.c.name)
        if tracked is not None:
            q = q.where(services_t.c.tracked == tracked)
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in await c.execute(q)]

    async def get_service(self, service_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as c:
            r = (await c.execute(select(services_t).where(services_t.c.id == service_id))).first()
        return dict(r._mapping) if r else None

    async def set_tracked(self, service_id: str, tracked: bool) -> bool:
        async with self.engine.begin() as c:
            res = await c.execute(
                update(services_t)
                .where(services_t.c.id == service_id)
                .values(tracked=tracked, tracked_at=utcnow() if tracked else None)
            )
        return bool(res.rowcount)

    async def save_custom_service(
        self, service_id: str, name: str, category: str, config: dict[str, Any], source: str
    ) -> None:
        now = utcnow()
        async with self.engine.begin() as c:
            await upsert(
                c,
                services_t,
                {
                    "id": service_id,
                    "name": name,
                    "category": category,
                    "source": source,
                    "tracked": True,
                    "config": config,
                    "created_at": now,
                    "tracked_at": now,
                },
                ["id"],
                update_cols=["name", "category", "config", "source"],
            )

    async def delete_service(self, service_id: str) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                delete(probe_results_t).where(probe_results_t.c.probe_id.like(f"{service_id}:%"))
            )
            await c.execute(delete(rollups_t).where(rollups_t.c.probe_id.like(f"{service_id}:%")))
            for t in (incident_updates_t,):
                await c.execute(
                    delete(t).where(
                        t.c.incident_id.in_(
                            select(incidents_t.c.id).where(incidents_t.c.service_id == service_id)
                        )
                    )
                )
            for t in (
                incidents_t,
                components_t,
                feed_status_t,
                probes_t,
                service_state_t,
                state_transitions_t,
            ):
                await c.execute(delete(t).where(t.c.service_id == service_id))
            await c.execute(delete(services_t).where(services_t.c.id == service_id))

    # -- probes --------------------------------------------------------------
    async def sync_probes(self, service_id: str, specs: list[ProbeSpec]) -> None:
        async with self.engine.begin() as c:
            ids = [s.id for s in specs]
            await c.execute(
                delete(probes_t).where(
                    and_(probes_t.c.service_id == service_id, probes_t.c.id.not_in(ids))
                )
            )
            rows = [
                {
                    "id": s.id,
                    "service_id": service_id,
                    "kind": s.kind,
                    "target": s.url or f"{s.host}:{s.port or ''}".rstrip(":"),
                    "config": s.to_dict(),
                    "enabled": True,
                }
                for s in specs
            ]
            await upsert(c, probes_t, rows, ["id"])

    async def list_probes(self, service_ids: Iterable[str] | None = None) -> list[ProbeSpec]:
        q = select(probes_t.c.config).where(probes_t.c.enabled.is_(True)).order_by(probes_t.c.id)
        if service_ids is not None:
            q = q.where(probes_t.c.service_id.in_(list(service_ids)))
        async with self.engine.connect() as c:
            return [ProbeSpec.from_dict(r[0]) for r in await c.execute(q)]

    async def insert_results(self, results: list[ProbeResult], agent_id: str | None = None) -> None:
        if not results:
            return
        rows = [
            {
                "probe_id": r.probe_id,
                "agent_id": agent_id,
                "ts": r.ts,
                "ok": r.ok,
                "latency_ms": r.latency_ms,
                "dns_ms": r.dns_ms,
                "connect_ms": r.connect_ms,
                "tls_ms": r.tls_ms,
                "ttfb_ms": r.ttfb_ms,
                "status_code": r.status_code,
                "error": r.error,
                "detail": r.detail or None,
            }
            for r in results
        ]
        async with self.engine.begin() as c:
            await c.execute(probe_results_t.insert(), rows)

    async def recent_results(
        self, since: datetime, agent: bool = False, probe_ids: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        q = (
            select(probe_results_t)
            .where(probe_results_t.c.ts >= since)
            .order_by(probe_results_t.c.ts)
        )
        q = q.where(
            probe_results_t.c.agent_id.is_not(None)
            if agent
            else probe_results_t.c.agent_id.is_(None)
        )
        if probe_ids is not None:
            q = q.where(probe_results_t.c.probe_id.in_(list(probe_ids)))
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in await c.execute(q)]

    async def latest_results(self, probe_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not probe_ids:
            return {}
        sub = (
            select(probe_results_t.c.probe_id, func.max(probe_results_t.c.ts).label("ts"))
            .where(probe_results_t.c.probe_id.in_(probe_ids), probe_results_t.c.agent_id.is_(None))
            .group_by(probe_results_t.c.probe_id)
            .subquery()
        )
        q = (
            select(probe_results_t)
            .join(
                sub,
                and_(
                    probe_results_t.c.probe_id == sub.c.probe_id, probe_results_t.c.ts == sub.c.ts
                ),
            )
            .where(probe_results_t.c.agent_id.is_(None))
        )
        async with self.engine.connect() as c:
            return {r.probe_id: dict(r._mapping) for r in await c.execute(q)}

    # -- feeds ----------------------------------------------------------------
    async def save_feed_error(self, service_id: str, error: str) -> None:
        now = utcnow()
        async with self.engine.begin() as c:
            existing = (
                await c.execute(
                    select(feed_status_t).where(feed_status_t.c.service_id == service_id)
                )
            ).first()
            await upsert(
                c,
                feed_status_t,
                {
                    "service_id": service_id,
                    "indicator": existing.indicator if existing else "none",
                    "description": existing.description if existing else None,
                    "ok": False,
                    "error": error[:500],
                    "fetched_at": now,
                    "last_ok_at": existing.last_ok_at if existing else None,
                },
                ["service_id"],
            )

    async def save_feed(self, service_id: str, snap: FeedSnapshot) -> FeedChange:
        now = utcnow()
        change = FeedChange([], [], [])
        async with self.engine.begin() as c:
            await upsert(
                c,
                feed_status_t,
                {
                    "service_id": service_id,
                    "indicator": snap.indicator,
                    "description": snap.description,
                    "ok": True,
                    "error": None,
                    "fetched_at": now,
                    "last_ok_at": now,
                },
                ["service_id"],
            )
            # Components: replace the set.
            await c.execute(delete(components_t).where(components_t.c.service_id == service_id))
            if snap.components:
                rows = [
                    {
                        "service_id": service_id,
                        "ext_id": comp.ext_id[:200],
                        "name": comp.name[:300],
                        "grp": comp.group,
                        "status": comp.status,
                        "region": comp.region,
                        "updated_at": now,
                    }
                    for comp in {comp.ext_id: comp for comp in snap.components}.values()
                ]
                for chunk in _chunks(rows, 200):
                    await c.execute(components_t.insert(), chunk)

            existing = {
                r.ext_id: dict(r._mapping)
                for r in await c.execute(
                    select(incidents_t).where(incidents_t.c.service_id == service_id)
                )
            }
            seen: set[str] = set()
            for inc in snap.incidents:
                ext = inc.ext_id[:300]
                seen.add(ext)
                values = {
                    "service_id": service_id,
                    "ext_id": ext,
                    "kind": inc.kind,
                    "title": inc.title,
                    "impact": inc.impact,
                    "status": inc.status,
                    "url": inc.url,
                    "started_at": inc.started_at,
                    "resolved_at": inc.resolved_at,
                    "scheduled_for": inc.scheduled_for,
                    "scheduled_until": inc.scheduled_until,
                    "components": inc.component_ids[:100],
                    "regions": inc.regions,
                    "last_seen_at": now,
                }
                prev = existing.get(ext)
                if prev is None:
                    values["first_seen_at"] = now
                    res = await c.execute(incidents_t.insert().values(**values))
                    inc_id = res.inserted_primary_key[0] if res.inserted_primary_key else None
                    if inc.active or inc.kind == "maintenance":
                        change.new_incidents.append({**values, "id": inc_id})
                else:
                    inc_id = prev["id"]
                    await c.execute(
                        update(incidents_t).where(incidents_t.c.id == inc_id).values(**values)
                    )
                    if prev["resolved_at"] is None and inc.resolved_at is not None:
                        change.resolved.append({**prev, **values})
                if inc_id is None or not inc.updates:
                    continue
                known = {
                    r[0]
                    for r in await c.execute(
                        select(incident_updates_t.c.ext_id).where(
                            incident_updates_t.c.incident_id == inc_id
                        )
                    )
                }
                fresh = [
                    {
                        "incident_id": inc_id,
                        "ext_id": u.ext_id[:300],
                        "status": u.status,
                        "body": u.body[:20000],
                        "created_at": u.created_at or now,
                    }
                    for u in inc.updates
                    if u.ext_id[:300] not in known
                ]
                if fresh:
                    await c.execute(incident_updates_t.insert(), fresh)
                    if prev is not None:
                        change.new_updates.extend(
                            {**f, "service_id": service_id, "title": inc.title} for f in fresh
                        )
            # Feeds like Statuspage drop incidents once resolved: close the ones that vanished.
            for ext, prev in existing.items():
                if ext in seen or prev["resolved_at"] is not None:
                    continue
                await c.execute(
                    update(incidents_t)
                    .where(incidents_t.c.id == prev["id"])
                    .values(
                        resolved_at=now,
                        status="resolved" if prev["kind"] == "incident" else "completed",
                    )
                )
                change.resolved.append({**prev, "resolved_at": now})
        return change

    async def feed_status(self) -> dict[str, dict[str, Any]]:
        async with self.engine.connect() as c:
            return {r.service_id: dict(r._mapping) for r in await c.execute(select(feed_status_t))}

    async def components(self, service_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(components_t)
                .where(components_t.c.service_id == service_id)
                .order_by(components_t.c.grp, components_t.c.name)
            )
            return [dict(r._mapping) for r in rows]

    async def region_components(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(
                    components_t.c.service_id,
                    components_t.c.name,
                    components_t.c.status,
                    components_t.c.region,
                )
                .join(services_t, services_t.c.id == components_t.c.service_id)
                .where(components_t.c.region.is_not(None), services_t.c.tracked.is_(True))
            )
            return [dict(r._mapping) for r in rows]

    # -- incidents ------------------------------------------------------------
    async def active_incidents(
        self, service_ids: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        q = (
            select(incidents_t)
            .where(incidents_t.c.resolved_at.is_(None))
            .order_by(incidents_t.c.started_at.desc())
        )
        if service_ids is not None:
            q = q.where(incidents_t.c.service_id.in_(list(service_ids)))
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in await c.execute(q)]

    async def list_incidents(
        self,
        service_id: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        tracked_only: bool = True,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        conds = []
        if service_id:
            conds.append(incidents_t.c.service_id == service_id)
        upcoming = and_(incidents_t.c.kind == "maintenance", incidents_t.c.status == "scheduled")
        if status == "active":
            conds.append(incidents_t.c.resolved_at.is_(None))
            conds.append(~upcoming)
        elif status == "upcoming":
            conds.append(incidents_t.c.resolved_at.is_(None))
            conds.append(upcoming)
        elif status == "resolved":
            conds.append(incidents_t.c.resolved_at.is_not(None))
        if kind:
            conds.append(incidents_t.c.kind == kind)
        if since:
            conds.append(
                or_(incidents_t.c.started_at >= since, incidents_t.c.resolved_at.is_(None))
            )
        base = select(incidents_t, services_t.c.name.label("service_name")).join(
            services_t, services_t.c.id == incidents_t.c.service_id
        )
        if tracked_only:
            conds.append(services_t.c.tracked.is_(True))
        where = and_(*conds) if conds else None
        q = base.where(where) if where is not None else base
        q = q.order_by(func.coalesce(incidents_t.c.started_at, incidents_t.c.first_seen_at).desc())
        cq = select(func.count()).select_from(
            incidents_t.join(services_t, services_t.c.id == incidents_t.c.service_id)
        )
        if where is not None:
            cq = cq.where(where)
        async with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in await c.execute(q.limit(limit).offset(offset))]
            total = (await c.execute(cq)).scalar_one()
        return rows, int(total)

    async def incident_updates(self, incident_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not incident_ids:
            return {}
        out: dict[int, list[dict[str, Any]]] = defaultdict(list)
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(incident_updates_t)
                .where(incident_updates_t.c.incident_id.in_(incident_ids))
                .order_by(incident_updates_t.c.created_at.desc())
            )
            for r in rows:
                out[r.incident_id].append(dict(r._mapping))
        return out

    async def get_incident(self, incident_id: int) -> dict[str, Any] | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(select(incidents_t).where(incidents_t.c.id == incident_id))
            ).first()
        return dict(r._mapping) if r else None

    # -- state ----------------------------------------------------------------
    async def get_states(self) -> dict[str, dict[str, Any]]:
        async with self.engine.connect() as c:
            return {
                r.service_id: dict(r._mapping) for r in await c.execute(select(service_state_t))
            }

    async def save_state(
        self, service_id: str, values: dict[str, Any], transition: bool, prev: dict[str, Any] | None
    ) -> None:
        now = utcnow()
        async with self.engine.begin() as c:
            await upsert(
                c,
                service_state_t,
                {"service_id": service_id, "updated_at": now, **values},
                ["service_id"],
            )
            if transition:
                await c.execute(
                    state_transitions_t.insert().values(
                        service_id=service_id,
                        from_state=prev["state"] if prev else None,
                        from_scope=prev["scope"] if prev else None,
                        to_state=values["state"],
                        scope=values["scope"],
                        reasons=values["reasons"],
                        ts=values["since"],
                    )
                )

    async def transitions(
        self, service_id: str | None = None, since: datetime | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        q = select(state_transitions_t).order_by(state_transitions_t.c.ts.desc()).limit(limit)
        if service_id:
            q = q.where(state_transitions_t.c.service_id == service_id)
        if since:
            q = q.where(state_transitions_t.c.ts >= since)
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in await c.execute(q)]

    # -- rollups / history ------------------------------------------------------
    async def rollup_5m(self, now: datetime | None = None) -> int:
        """Aggregate finished 5-minute buckets since the watermark. Returns buckets written."""
        now = now or utcnow()
        end = floor_time(now, 300)
        start = await self.get_setting("rollup_5m_watermark")
        start_dt = datetime.fromisoformat(start) if start else end - timedelta(days=1)
        if start_dt >= end:
            return 0
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(
                    probe_results_t.c.probe_id,
                    probe_results_t.c.agent_id,
                    probe_results_t.c.ts,
                    probe_results_t.c.ok,
                    probe_results_t.c.latency_ms,
                ).where(probe_results_t.c.ts >= start_dt, probe_results_t.c.ts < end)
            )
            groups: dict[tuple[str, str, datetime], list[tuple[bool, float | None]]] = defaultdict(
                list
            )
            for r in rows:
                groups[(r.probe_id, r.agent_id or "", floor_time(r.ts, 300))].append(
                    (r.ok, r.latency_ms)
                )
        out = [_rollup_row(k, v, "5m") for k, v in groups.items()]
        async with self.engine.begin() as c:
            for chunk in _chunks(out, 300):
                await upsert(c, rollups_t, chunk, ["probe_id", "agent_id", "res", "bucket"])
        await self._rollup_days(start_dt, end)
        await self.set_setting("rollup_5m_watermark", end.isoformat())
        return len(out)

    async def _rollup_days(self, start: datetime, end: datetime) -> None:
        day_start = floor_time(start, 86400)
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(rollups_t).where(
                    rollups_t.c.res == "5m",
                    rollups_t.c.bucket >= day_start,
                    rollups_t.c.bucket < end,
                )
            )
            days: dict[tuple[str, str, datetime], list[Any]] = defaultdict(list)
            for r in rows:
                days[(r.probe_id, r.agent_id, floor_time(r.bucket, 86400))].append(r)
        out = []
        for (pid, aid, day), items in days.items():
            n = sum(i.n for i in items)
            n_ok = sum(i.n_ok for i in items)
            lat = [(i.avg_ms, i.n_ok) for i in items if i.avg_ms is not None and i.n_ok]
            w = sum(k for _, k in lat)
            out.append(
                {
                    "probe_id": pid,
                    "agent_id": aid,
                    "res": "1d",
                    "bucket": day,
                    "n": n,
                    "n_ok": n_ok,
                    "avg_ms": (sum(a * k for a, k in lat) / w) if w else None,
                    # Approximation: median of bucket p50s, max-ish (p95) of bucket p95s.
                    "p50_ms": percentile([i.p50_ms for i in items if i.p50_ms is not None], 0.5)
                    if any(i.p50_ms is not None for i in items)
                    else None,
                    "p95_ms": percentile([i.p95_ms for i in items if i.p95_ms is not None], 0.95)
                    if any(i.p95_ms is not None for i in items)
                    else None,
                }
            )
        async with self.engine.begin() as c:
            for chunk in _chunks(out, 300):
                await upsert(c, rollups_t, chunk, ["probe_id", "agent_id", "res", "bucket"])

    async def rollups(
        self, probe_ids: list[str], res: str, since: datetime, agent_id: str = ""
    ) -> list[dict[str, Any]]:
        if not probe_ids:
            return []
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(rollups_t)
                .where(
                    rollups_t.c.probe_id.in_(probe_ids),
                    rollups_t.c.res == res,
                    rollups_t.c.bucket >= since,
                    rollups_t.c.agent_id == agent_id,
                )
                .order_by(rollups_t.c.bucket)
            )
            return [dict(r._mapping) for r in rows]

    async def retention(self, raw_days: int, r5m_days: int, incident_days: int) -> dict[str, int]:
        now = utcnow()
        counts: dict[str, int] = {}
        async with self.engine.begin() as c:
            res = await c.execute(
                delete(probe_results_t).where(probe_results_t.c.ts < now - timedelta(days=raw_days))
            )
            counts["probe_results"] = res.rowcount or 0
            res = await c.execute(
                delete(rollups_t).where(
                    rollups_t.c.res == "5m", rollups_t.c.bucket < now - timedelta(days=r5m_days)
                )
            )
            counts["rollups_5m"] = res.rowcount or 0
            old = select(incidents_t.c.id).where(
                incidents_t.c.resolved_at < now - timedelta(days=incident_days)
            )
            await c.execute(
                delete(incident_updates_t).where(incident_updates_t.c.incident_id.in_(old))
            )
            res = await c.execute(
                delete(incidents_t).where(
                    incidents_t.c.resolved_at < now - timedelta(days=incident_days)
                )
            )
            counts["incidents"] = res.rowcount or 0
            res = await c.execute(
                delete(state_transitions_t).where(
                    state_transitions_t.c.ts < now - timedelta(days=incident_days)
                )
            )
            counts["state_transitions"] = res.rowcount or 0
            await c.execute(
                delete(notify_log_t).where(notify_log_t.c.ts < now - timedelta(days=90))
            )
            await c.execute(
                delete(agent_nonces_t).where(agent_nonces_t.c.ts < now - timedelta(minutes=10))
            )
            await c.execute(delete(chatter_t).where(chatter_t.c.bucket < now - timedelta(days=30)))
        return counts

    # -- users ----------------------------------------------------------------
    async def user_count(self) -> int:
        async with self.engine.connect() as c:
            return int((await c.execute(select(func.count()).select_from(users_t))).scalar_one())

    async def get_user(self, username: str) -> dict[str, Any] | None:
        async with self.engine.connect() as c:
            r = (await c.execute(select(users_t).where(users_t.c.username == username))).first()
        return dict(r._mapping) if r else None

    async def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        async with self.engine.connect() as c:
            r = (await c.execute(select(users_t).where(users_t.c.id == user_id))).first()
        return dict(r._mapping) if r else None

    async def create_user(self, username: str, password_hash: str) -> int:
        async with self.engine.begin() as c:
            res = await c.execute(
                users_t.insert().values(
                    username=username, password_hash=password_hash, created_at=utcnow()
                )
            )
            return int(res.inserted_primary_key[0])  # type: ignore[index]

    async def set_password(self, user_id: int, password_hash: str) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(users_t).where(users_t.c.id == user_id).values(password_hash=password_hash)
            )

    # -- agents ----------------------------------------------------------------
    async def list_agents(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            return [
                dict(r._mapping)
                for r in await c.execute(select(agents_t).order_by(agents_t.c.name))
            ]

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as c:
            r = (await c.execute(select(agents_t).where(agents_t.c.id == agent_id))).first()
        return dict(r._mapping) if r else None

    async def create_agent(
        self, agent_id: str, name: str, location: str | None, lat: float | None, lon: float | None
    ) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                agents_t.insert().values(
                    id=agent_id,
                    name=name,
                    location=location,
                    lat=lat,
                    lon=lon,
                    created_at=utcnow(),
                    revoked=False,
                )
            )

    async def update_agent(self, agent_id: str, **values: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(update(agents_t).where(agents_t.c.id == agent_id).values(**values))

    async def delete_agent(self, agent_id: str) -> None:
        async with self.engine.begin() as c:
            await c.execute(delete(probe_results_t).where(probe_results_t.c.agent_id == agent_id))
            await c.execute(delete(rollups_t).where(rollups_t.c.agent_id == agent_id))
            await c.execute(delete(agents_t).where(agents_t.c.id == agent_id))

    async def use_nonce(self, nonce: str) -> bool:
        """Record a nonce; False if it was already used (replay)."""
        async with self.engine.begin() as c:
            exists = (
                await c.execute(
                    select(agent_nonces_t.c.nonce).where(agent_nonces_t.c.nonce == nonce)
                )
            ).first()
            if exists:
                return False
            await c.execute(agent_nonces_t.insert().values(nonce=nonce, ts=utcnow()))
        return True

    # -- notifications -----------------------------------------------------------
    async def list_channels(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            return [
                dict(r._mapping)
                for r in await c.execute(select(notify_channels_t).order_by(notify_channels_t.c.id))
            ]

    async def list_rules(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            return [
                dict(r._mapping)
                for r in await c.execute(select(notify_rules_t).order_by(notify_rules_t.c.id))
            ]

    async def log_notification(
        self,
        channel_id: int | None,
        service_id: str | None,
        kind: str,
        title: str,
        ok: bool,
        error: str | None,
    ) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                notify_log_t.insert().values(
                    channel_id=channel_id,
                    service_id=service_id,
                    kind=kind,
                    title=title[:500],
                    ts=utcnow(),
                    ok=ok,
                    error=(error or "")[:500] or None,
                )
            )

    async def notification_log(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            rows = await c.execute(
                select(notify_log_t).order_by(notify_log_t.c.ts.desc()).limit(limit)
            )
            return [dict(r._mapping) for r in rows]

    # -- stats ----------------------------------------------------------------
    async def table_counts(self) -> dict[str, int]:
        out = {}
        async with self.engine.connect() as c:
            for t in (services_t, probe_results_t, rollups_t, incidents_t, state_transitions_t):
                out[t.name] = int(
                    (await c.execute(select(func.count()).select_from(t))).scalar_one()
                )
        return out


def _rollup_row(
    key: tuple[str, str, datetime], vals: list[tuple[bool, float | None]], res: str
) -> dict[str, Any]:
    pid, aid, bucket = key
    lats = [lat for ok, lat in vals if ok and lat is not None]
    return {
        "probe_id": pid,
        "agent_id": aid,
        "res": res,
        "bucket": bucket,
        "n": len(vals),
        "n_ok": sum(1 for ok, _ in vals if ok),
        "avg_ms": sum(lats) / len(lats) if lats else None,
        "p50_ms": percentile(lats, 0.5) if lats else None,
        "p95_ms": percentile(lats, 0.95) if lats else None,
    }


def _chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]
