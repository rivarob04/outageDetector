"""A small asyncio job scheduler: per-job intervals, jitter, bounded concurrency.

Jobs are coroutines. A failing job is logged and rescheduled with exponential
backoff (up to 10× its interval); it can never kill the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("pulse.scheduler")

JobFn = Callable[[], Awaitable[None]]


@dataclass
class Job:
    name: str
    fn: JobFn
    interval: float
    jitter: float = 0.1  # ± fraction of interval
    errors: int = 0
    runs: int = 0
    last_run: float | None = None
    last_error: str | None = None
    running: bool = False
    removed: bool = False

    def next_delay(self) -> float:
        base = self.interval * (min(2**self.errors, 10) if self.errors else 1)
        spread = base * self.jitter
        return max(0.01, base + random.uniform(-spread, spread))


@dataclass(order=True)
class _Entry:
    due: float
    seq: int
    job: Job = field(compare=False)


class Scheduler:
    def __init__(
        self, max_concurrency: int = 20, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._heap: list[_Entry] = []
        self._jobs: dict[str, Job] = {}
        self._seq = itertools.count()
        self._sem = asyncio.Semaphore(max_concurrency)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running: set[asyncio.Task[None]] = set()
        self._clock = clock
        self._stopping = False

    # -- job management -------------------------------------------------------
    def add(
        self,
        name: str,
        fn: JobFn,
        interval: float,
        *,
        jitter: float = 0.1,
        initial_delay: float | None = None,
    ) -> Job:
        """Add (or replace) a job. By default the first run is spread over one interval."""
        self.remove(name)
        job = Job(name, fn, interval, jitter)
        self._jobs[name] = job
        delay = random.uniform(0, min(interval, 10.0)) if initial_delay is None else initial_delay
        self._push(job, delay)
        return job

    def remove(self, name: str) -> None:
        job = self._jobs.pop(name, None)
        if job:
            job.removed = True

    def remove_prefix(self, prefix: str) -> None:
        for name in [n for n in self._jobs if n.startswith(prefix)]:
            self.remove(name)

    def jobs(self) -> dict[str, Job]:
        return dict(self._jobs)

    def trigger(self, name: str) -> None:
        """Run a job as soon as possible (e.g. a "check now" button)."""
        job = self._jobs.get(name)
        if job:
            self._push(job, 0)

    def _push(self, job: Job, delay: float) -> None:
        heapq.heappush(self._heap, _Entry(self._clock() + delay, next(self._seq), job))
        self._wake.set()

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._loop(), name="pulse-scheduler")

    async def stop(self, grace_s: float = 5.0) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._running:
            _done, pending = await asyncio.wait(self._running, timeout=grace_s)
            for t in pending:
                t.cancel()

    async def _loop(self) -> None:
        while not self._stopping:
            if not self._heap:
                self._wake.clear()
                await self._wake.wait()
                continue
            entry = self._heap[0]
            delay = entry.due - self._clock()
            if delay > 0:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                continue
            heapq.heappop(self._heap)
            job = entry.job
            if job.removed or self._jobs.get(job.name) is not job:
                continue
            if job.running:  # never overlap a job with itself
                self._push(job, job.next_delay())
                continue
            task = asyncio.create_task(self._run(job), name=f"job:{job.name}")
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    async def _run(self, job: Job) -> None:
        job.running = True
        try:
            async with self._sem:
                await job.fn()
            job.errors = 0
            job.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.errors += 1
            job.last_error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("job %s failed (%d): %s", job.name, job.errors, job.last_error)
        finally:
            job.running = False
            job.runs += 1
            job.last_run = self._clock()
            if not job.removed and not self._stopping:
                self._push(job, job.next_delay())
