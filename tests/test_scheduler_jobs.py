"""Smoke test for the scheduler's nurture job wrapper.

We can't (easily) spin up python-telegram-bot's JobQueue in a unit test, so
we just call the async handler directly with a stub context and verify it
invokes `nurture.tick`. The wiring inside `init_scheduler` (interval, name)
is glanced at but not invoked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_nurture_tick_handler_invokes_tick(monkeypatch):
    from roost.bot import scheduler
    seen: dict = {}

    def fake_tick(*, now_utc=None, max_per_tick=50):
        seen["called"] = True
        return {"ticked_at": "now", "due": 1, "sent": 1,
                "held": 0, "completed": 0, "errors": 0}

    monkeypatch.setattr("roost.extras.lead_nurture.services.nurture.tick", fake_tick)
    asyncio.run(scheduler._nurture_tick(SimpleNamespace()))
    assert seen.get("called") is True


def test_nurture_tick_handler_swallows_exceptions(monkeypatch, caplog):
    from roost.bot import scheduler

    def boom(**kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr("roost.extras.lead_nurture.services.nurture.tick", boom)
    # Should not raise — the handler logs and returns
    asyncio.run(scheduler._nurture_tick(SimpleNamespace()))


def test_init_scheduler_registers_nurture_job():
    """`init_scheduler` should add a job named 'nurture_tick' to the JobQueue."""
    from roost.bot import scheduler
    jobs: list[dict] = []

    class FakeJobQueue:
        def run_daily(self, fn, **kw):
            jobs.append({"kind": "daily", "name": kw.get("name")})

        def run_repeating(self, fn, **kw):
            jobs.append({"kind": "repeating", "name": kw.get("name"),
                         "interval": kw.get("interval")})

    app = SimpleNamespace(job_queue=FakeJobQueue())
    scheduler.init_scheduler(app)
    names = [j["name"] for j in jobs]
    assert "nurture_tick" in names
    nurture_job = next(j for j in jobs if j["name"] == "nurture_tick")
    assert nurture_job["kind"] == "repeating"
    assert nurture_job["interval"] == 60
