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

    Raises :class:`TaskError` if the task finishes with a non-OK exit status or does
    not finish within ``timeout`` seconds.

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
        nonlocal seen
        if log_fn is None or on_lines is None:
            return
        while True:
            batch = [(n, text) for n, text in log_fn(seen) if n > seen]
            if not batch:
                return
            on_lines(batch)
            seen = max(n for n, _ in batch)

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
            raise TaskError(f"Task {upid} did not finish within {timeout:.0f}s")
        sleep(poll_interval)
