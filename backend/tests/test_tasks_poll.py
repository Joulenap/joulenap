"""poll_task's timeout is a *no-progress* timeout when the log is tailed (#37): a task that
keeps writing never trips it, however long it runs; a silent one still does.

The second half covers #53: losing contact with the server is not the task failing, so the
loop rides out a blip instead of throwing away a job that is still running."""

from __future__ import annotations

import pytest

from app.connectors import _tasks
from app.connectors._tasks import poll_task
from app.connectors.errors import ApiError, TaskCancelled, TaskError


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


# --- losing contact mid-task (#53) -------------------------------------------


def _flaky(fail_times: int, then_stopped_after: int = 0):
    """A status_fn that raises ApiError ``fail_times`` times before answering normally."""
    calls = {"fail": 0, "ok": 0}

    def status(_upid):
        if calls["fail"] < fail_times:
            calls["fail"] += 1
            raise ApiError("GET /status failed: the read operation timed out")
        calls["ok"] += 1
        if calls["ok"] > then_stopped_after:
            return {"status": "stopped", "exitstatus": "OK"}
        return {"status": "running"}

    return status


def test_a_blip_is_survived_and_reported_once(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(_tasks, "_ERROR_GRACE", 300.0)
    seen: list[ApiError] = []

    # 4 failed polls x 60s = 240s of silence, inside the 300s window.
    status = poll_task(
        _flaky(4), "UPID:x", poll_interval=60, timeout=6 * 3600,
        sleep=clock.sleep, on_error=seen.append,
    )

    assert status["exitstatus"] == "OK"  # the task was fine all along
    assert len(seen) == 1  # one line in the timeline, not one per poll
    assert "read operation timed out" in str(seen[0])


def test_contact_restored_resets_the_window(monkeypatch):
    """Two separate blips, neither long enough: a flaky link must not accumulate."""
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(_tasks, "_ERROR_GRACE", 300.0)
    script = [ApiError("blip 1")] * 4 + [{"status": "running"}] + [ApiError("blip 2")] * 4
    seen: list[ApiError] = []

    def status(_upid):
        if not script:
            return {"status": "stopped", "exitstatus": "OK"}
        step = script.pop(0)
        if isinstance(step, ApiError):
            raise step
        return step

    result = poll_task(
        status, "UPID:x", poll_interval=60, timeout=6 * 3600,
        sleep=clock.sleep, on_error=seen.append,
    )

    assert result["exitstatus"] == "OK"
    assert [str(e) for e in seen] == ["blip 1", "blip 2"]  # reported once per outage


def test_an_outage_longer_than_the_window_raises_the_original_error(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(_tasks, "_ERROR_GRACE", 300.0)

    with pytest.raises(ApiError) as info:
        poll_task(
            _flaky(10_000), "UPID:x", poll_interval=60, timeout=6 * 3600, sleep=clock.sleep,
        )

    # The caller's failure line still names what actually went wrong, not a synthetic one.
    assert "read operation timed out" in str(info.value)
    assert clock.now >= 300


def test_a_non_api_error_is_not_survivable(monkeypatch):
    """A DB failure inside on_lines, or a bug in a callback, must surface immediately."""
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)
    seen: list[ApiError] = []

    def status(_upid):
        raise RuntimeError("database is locked")

    with pytest.raises(RuntimeError):
        poll_task(status, "UPID:x", poll_interval=60, sleep=clock.sleep, on_error=seen.append)

    assert seen == [] and clock.now == 0  # no waiting around for a bug to fix itself


def test_a_run_can_still_be_cancelled_while_contact_is_lost(monkeypatch):
    """The grace window must not make a stop button unresponsive for five minutes."""
    clock = _Clock()
    monkeypatch.setattr(_tasks.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(_tasks, "_ERROR_GRACE", 300.0)
    polls = {"n": 0}

    def status(_upid):
        polls["n"] += 1
        raise ApiError("no route to host")

    with pytest.raises(TaskCancelled):
        poll_task(
            status, "UPID:x", poll_interval=60, sleep=clock.sleep,
            should_cancel=lambda: polls["n"] >= 2,
        )

    assert clock.now < 300  # cancelled inside the window, not after it
