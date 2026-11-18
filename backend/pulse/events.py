"""In-process pub/sub for live updates (SSE) and integrations."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

Handler = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    type: str  # state | incident | connectivity | agent
    data: dict[str, Any]
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventBus:
    def __init__(self, queue_size: int = 200) -> None:
        self._subs: set[asyncio.Queue[Event]] = set()
        self._handlers: list[Handler] = []
        self._size = queue_size
        self._tasks: set[asyncio.Task[None]] = set()

    def add_handler(self, handler: Handler) -> None:
        """Handlers run as background tasks; failures are isolated."""
        self._handlers.append(handler)

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        ev = Event(type_, data)
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()  # drop the oldest for slow clients
            q.put_nowait(ev)
        for h in self._handlers:
            task = asyncio.create_task(self._safe(h, ev))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _safe(self, h: Handler, ev: Event) -> None:
        try:
            await h(ev)
        except Exception:
            import logging

            logging.getLogger("pulse.events").exception("event handler failed")

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(self._size)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
