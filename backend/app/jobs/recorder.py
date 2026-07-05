"""Persist a run and its steps/logs to SQLite as the job progresses.

A :class:`RunRecorder` owns one DB session and commits after every step and log line,
so an in-flight run (and its current step) is visible to ``GET /api/status`` and
``GET /api/runs/{id}`` while the cycle is still running.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db import make_session
from ..db.models import (
    LogEvent,
    LogLevel,
    Run,
    RunKind,
    RunStatus,
    RunStep,
    RunTrigger,
    StepName,
    StepStatus,
    TaskLogLine,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunRecorder:
    """Records a single run. Use as a context manager so the session is always closed
    and an unfinished run is marked failed even if the caller crashes unexpectedly."""

    def __init__(
        self,
        kind: RunKind,
        trigger: RunTrigger,
        *,
        session_factory: Callable[[], Session] = make_session,
    ):
        self._session = session_factory()
        try:
            self.run = Run(kind=kind, trigger=trigger, status=RunStatus.RUNNING)
            self._session.add(self.run)
            self._session.commit()
        except Exception:
            # A DB error at run start (e.g. locked) must not leak the just-opened session.
            self._session.close()
            raise
        self._finished = False

    @property
    def run_id(self) -> int:
        return self.run.id

    # --- logging -------------------------------------------------------------

    def log(self, level: LogLevel, message: str) -> None:
        self._session.add(LogEvent(run_id=self.run.id, level=level, message=message))
        self._session.commit()

    def task_log(self, step: StepName, source: str, lines: list[tuple[int, str]]) -> None:
        """Append a batch of raw task-log lines for the live Task-log panel.

        ``lines`` is a list of ``(line_no, text)`` pairs from the PVE/PBS task tailer;
        committed per batch so an in-flight task streams to ``GET /api/tasklog``.
        """
        for line_no, text in lines:
            self._session.add(
                TaskLogLine(
                    run_id=self.run.id,
                    step=step.value,
                    source=source,
                    line_no=line_no,
                    text=text,
                )
            )
        self._session.commit()

    # --- steps ---------------------------------------------------------------

    @contextmanager
    def step(self, name: StepName) -> Iterator[RunStep]:
        """Run a step: persisted RUNNING on entry, SUCCESS on clean exit, FAILURE (and
        re-raised) on exception. The yielded row can carry a ``detail`` (e.g. task UPID)."""
        step = RunStep(run_id=self.run.id, name=name, status=StepStatus.RUNNING)
        self._session.add(step)
        self._session.commit()
        self.log(LogLevel.INFO, f"{name.value}: started")
        try:
            yield step
        except Exception as exc:
            step.status = StepStatus.FAILURE
            step.finished_at = _utcnow()
            step.detail = str(exc)
            self.log(LogLevel.ERROR, f"{name.value}: {exc}")
            self._session.commit()
            raise
        else:
            step.finished_at = _utcnow()
            # Only auto-complete to SUCCESS if the body didn't set a status itself — this lets
            # a caller record a failed-but-non-fatal step (e.g. a best-effort power-off) by
            # setting step.status = FAILURE and returning normally.
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.SUCCESS
                self.log(LogLevel.OK, f"{name.value}: done")
            self._session.commit()

    def skip_step(self, name: StepName, detail: str | None = None) -> None:
        """Record a step that was intentionally not run (e.g. GC when the toggle is off)."""
        self._session.add(
            RunStep(
                run_id=self.run.id,
                name=name,
                status=StepStatus.SKIPPED,
                finished_at=_utcnow(),
                detail=detail,
            )
        )
        if detail:
            self.log(LogLevel.INFO, f"{name.value}: skipped ({detail})")
        else:
            self.log(LogLevel.INFO, f"{name.value}: skipped")
        self._session.commit()

    # --- finalisation --------------------------------------------------------

    def finish(self, status: RunStatus, *, error: str | None = None, **summary: int) -> None:
        self.run.status = status
        self.run.finished_at = _utcnow()
        if error is not None:
            self.run.error = error
        for key, value in summary.items():
            setattr(self.run, key, value)
        self._session.commit()
        self._finished = True

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        # Safety net: if the job body raised before finishing, mark the run failed.
        if not self._finished:
            self.finish(RunStatus.FAILURE, error=str(exc) if exc else "unexpected error")
        self.close()
