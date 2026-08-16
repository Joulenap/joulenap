"""poll_task's timeout is a *no-progress* timeout when the log is tailed (#37): a task that
keeps writing never trips it, however long it runs; a silent one still does."""

from __future__ import annotations

import pytest

from app.connectors import _tasks
from app.connectors._tasks import poll_task
from app.connectors.errors import TaskError


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _running_then_stopped(after_polls: int):
    calls = {"n": 0}

    def status(_upid):
        calls["n"] += 1
        if calls["n"] > after_polls:
            return {"status": "stopped", "exitstatus": "OK"}
        return {"status": "running"}

    return status


def test_a_talking_task_outlives_the_timeout(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)
    # 30 polls x 60s = 30 minutes of task, timeout 100s: dead if the cap were absolute.
    inner = _running_then_stopped(30)
    fresh = {"line": False}

    def status_fn(upid):
        fresh["line"] = True  # one new log line per poll
        return inner(upid)

    def log_fn(offset):
        if not fresh["line"]:
            return []  # caught up
        fresh["line"] = False
        return [(offset + 1, "still syncing")]

    status = poll_task(
        status_fn, "UPID:x", poll_interval=60, timeout=100,
        sleep=clock.sleep, log_fn=log_fn, on_lines=lambda _b: None,
    )
    assert status["exitstatus"] == "OK"
    assert clock.now >= 30 * 60


def test_a_silent_task_fails_after_the_timeout_and_says_so(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)

    with pytest.raises(TaskError) as info:
        poll_task(
            _running_then_stopped(10_000), "UPID:x", poll_interval=60, timeout=100,
            sleep=clock.sleep, log_fn=lambda _o: [], on_lines=lambda _b: None,
        )
    assert "produced no output for 100s" in str(info.value)
    assert info.value.exit_status == "timeout 100s"  # not None -> not "unknown status"


def test_timeouts_read_in_hours_when_they_are_hours():
    from app.connectors._tasks import _human

    assert (_human(21600), _human(5400), _human(90)) == ("6h", "1.5h", "90s")


def test_without_a_tail_the_timeout_is_the_total_wait(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)

    with pytest.raises(TaskError) as info:
        poll_task(
            _running_then_stopped(10_000), "UPID:x", poll_interval=60, timeout=100,
            sleep=clock.sleep,
        )
    assert "did not finish within 100s" in str(info.value)
