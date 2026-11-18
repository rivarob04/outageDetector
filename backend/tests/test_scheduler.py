from __future__ import annotations

import asyncio

from pulse.scheduler import Job, Scheduler


async def test_runs_jobs_repeatedly_and_isolates_errors() -> None:
    sched = Scheduler(max_concurrency=4)
    counts = {"good": 0, "bad": 0}

    async def good() -> None:
        counts["good"] += 1

    async def bad() -> None:
        counts["bad"] += 1
        raise RuntimeError("boom")

    sched.add("good", good, 0.02, jitter=0, initial_delay=0)
    bad_job = sched.add("bad", bad, 0.02, jitter=0, initial_delay=0)
    sched.start()
    await asyncio.sleep(0.25)
    await sched.stop()
    assert counts["good"] >= 5
    assert counts["bad"] >= 1
    assert bad_job.errors >= 1 and "boom" in (bad_job.last_error or "")
    assert counts["bad"] < counts["good"]  # backoff slowed it down


async def test_remove_and_trigger() -> None:
    sched = Scheduler()
    hits: list[str] = []

    async def a() -> None:
        hits.append("a")

    sched.add("svc:a", a, 60, initial_delay=60)
    sched.start()
    sched.trigger("svc:a")
    await asyncio.sleep(0.05)
    assert hits == ["a"]
    sched.remove_prefix("svc:")
    assert sched.jobs() == {}
    sched.trigger("svc:a")
    await asyncio.sleep(0.05)
    assert hits == ["a"]
    await sched.stop()


async def test_no_overlap() -> None:
    sched = Scheduler()
    active = 0
    peak = 0

    async def slow() -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    sched.add("slow", slow, 0.005, jitter=0, initial_delay=0)
    sched.start()
    await asyncio.sleep(0.2)
    await sched.stop()
    assert peak == 1


def test_backoff_caps_at_ten_times() -> None:
    job = Job("x", lambda: None, 60, jitter=0)  # type: ignore[arg-type,return-value]
    job.errors = 1
    assert job.next_delay() == 120
    job.errors = 8
    assert job.next_delay() == 600
