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

from .errors import TaskCancelled, TaskError

# One task-log line as returned by the tailer: (line number, text). The line number is
# the task's own 1-based ``n``; the tailer uses it as the offset cursor for the next fetch.
LogLine = tuple[int, str]


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
    """
    deadline = time.monotonic() + timeout
    seen = 0  # highest line number handed to on_lines so far (the fetch offset)

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
            # write before we walked away.
            drain()
            raise TaskCancelled(f"Wait for task {upid} cancelled")
        status = status_fn(upid)
        drain()  # pull whatever's been logged since the last tick (tail after stop)
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
