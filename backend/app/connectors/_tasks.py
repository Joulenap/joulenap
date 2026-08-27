"""Shared task-polling loop for PVE and PBS background tasks.

Both expose ``.../tasks/{upid}/status`` returning ``{status, exitstatus}`` with the
same semantics, so the wait loop lives here once. The same loop can also *tail* the
task's log (``.../tasks/{upid}/log``) so the UI can narrate a running backup/GC/verify
live — pass ``log_fn``/``on_lines`` and each poll pulls any new lines as a side-effect.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .errors import ApiError, TaskCancelled, TaskError

# One task-log line as returned by the tailer: (line number, text). The line number is
# the task's own 1-based ``n``; the tailer uses it as the offset cursor for the next fetch.
LogLine = tuple[int, str]

# How long the poll loop keeps trying after it loses contact with the server, before it
# gives up and fails the run (#53). A NIC that resets, a switch that reboots, a box that
# briefly drops off the LAN: the remote task keeps running through all of those, so failing
# on the first errored poll throws away a job that is still healthy, and reports a *false*
# failure for one that goes on to succeed.
# ponytail: one fixed window for every deployment. If a WAN-separated pair ever needs
# longer, this becomes a per-device config knob.
_ERROR_GRACE = 300.0


def _human(seconds: float) -> str:
    """``21600`` -> ``6h``, ``90`` -> ``90s``: what the failure line shows the user."""
    return f"{seconds / 3600:g}h" if seconds >= 3600 else f"{seconds:.0f}s"


def poll_task(
    status_fn: Callable[[str], dict[str, Any]],
    upid: str,
    poll_interval: float = 3.0,
    timeout: float = 6 * 3600,
    sleep: Callable[[float], None] = time.sleep,
    *,
    log_fn: Callable[[int], list[LogLine]] | None = None,
    on_lines: Callable[[list[LogLine]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_error: Callable[[ApiError], None] | None = None,
) -> dict[str, Any]:
    """Poll ``status_fn(upid)`` until the task stops; return its final status.

    Raises :class:`TaskError` if the task finishes with a non-OK exit status, or goes
    ``timeout`` seconds without a sign of life. When the log is being tailed that means *no
    new log line* for that long — a healthy 30-hour first sync to an S3 datastore keeps
    talking and never trips it, a task hung in silence still fails; without a tail there is
    no progress to see, so ``timeout`` is simply the total wait.

    ``should_cancel`` makes the wait interruptible: it is consulted once per poll, and a
    True raises :class:`TaskCancelled` — the caller decides whether to also stop the remote
    task. This is the only way out of a long wait, since a blocking thread can't be
    interrupted from outside; the poll interval is therefore the cancel latency.

    If both ``log_fn`` and ``on_lines`` are given, each poll also drains any new task-log
    lines: ``log_fn(offset)`` returns lines numbered greater than ``offset`` (empty once
    caught up) and ``on_lines`` is handed each new batch. The final poll (task stopped)
    drains the remaining tail, so no lines are lost.

    Losing contact with the server is *not* a failure on its own: an :class:`ApiError` from
    the status call starts a :data:`_ERROR_GRACE` window and the loop keeps trying, because
    the remote task is still running and usually still fine (see #53). The first drop is
    reported to ``on_error`` so the caller can say so in the run timeline; the original error
    is re-raised unchanged if contact never comes back.
    """
    deadline = time.monotonic() + timeout
    seen = 0  # highest line number handed to on_lines so far (the fetch offset)
    lost_at: float | None = None  # when contact dropped, None while the server is answering

    def drain() -> None:
        nonlocal seen, deadline
        if log_fn is None or on_lines is None:
            return
        while True:
            batch = [(n, text) for n, text in log_fn(seen) if n > seen]
            if not batch:
                return
            on_lines(batch)
            seen = max(n for n, _ in batch)
            deadline = time.monotonic() + timeout  # output = progress: the clock restarts

    while True:
        if should_cancel is not None and should_cancel():
            # Drain first so the task-log panel keeps the last lines the task managed to
            # write before we walked away. Best effort, since the reason we are here may
            # well be that the box stopped answering.
            try:
                drain()
            except ApiError:
                pass
            raise TaskCancelled(f"Wait for task {upid} cancelled")
        try:
            status = status_fn(upid)
            drain()  # pull whatever's been logged since the last tick (tail after stop)
        except ApiError as exc:
            # Only transport/API failures are survivable here. Anything else (a DB error
            # inside on_lines, a bug in the caller's callbacks) still ends the wait.
            now = time.monotonic()
            if lost_at is None:
                lost_at = now
                if on_error is not None:
                    on_error(exc)
            if now - lost_at >= _ERROR_GRACE:
                raise
            sleep(poll_interval)
            continue
        lost_at = None  # answered: whatever that was, it is over
        if status.get("status") == "stopped":
            exit_status = status.get("exitstatus")
            if exit_status != "OK":
                raise TaskError(
                    f"Task {upid} finished with status {exit_status!r}",
                    exit_status=exit_status,
                )
            return status
        if time.monotonic() >= deadline:
            what = "produced no output for" if log_fn is not None else "did not finish within"
            # A cause of its own, not None: the sync/GC/verify failure line otherwise reads
            # "failed (unknown status)" for a task that PBS is very possibly still running.
            raise TaskError(
                f"Task {upid} {what} {_human(timeout)}", exit_status=f"timeout {_human(timeout)}"
            )
        sleep(poll_interval)
