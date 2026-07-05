"""ORM models for run history and the activity log.

These back ``GET /api/logs`` and ``GET /api/runs/{id}`` (wired in later milestones) and
mirror the dashboard's "Activity log" and "Last run" panels in the design.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """A timezone-aware ``DateTime`` that always reads back as UTC-aware.

    SQLite has no native tz type and returns naive datetimes on read, so a value stored
    as ``14:00+00:00`` comes back as a naive ``14:00`` — which then serializes to JSON
    without an offset and gets misread as *local* time by the frontend (shifting every
    displayed timestamp by the viewer's UTC offset). This normalizes values to UTC on the
    way in and re-attaches ``UTC`` on the way out so they serialize with an offset.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        # Treat a naive value as UTC; otherwise convert to UTC before storing.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RunKind(StrEnum):
    BACKUP = "backup"
    GC = "gc"
    VERIFY = "verify"
    CYCLE = "cycle"  # full wake -> backup -> maintenance -> poweroff cycle


class RunTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"


class LogLevel(StrEnum):
    INFO = "INFO"
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"


class StepName(StrEnum):
    """Stages of the backup cycle (and the standalone GC run), in execution order."""

    WAKE = "wake"
    WAIT = "wait"
    PRECHECK = "precheck"
    BACKUP = "backup"
    GC = "gc"
    VERIFY = "verify"
    POWEROFF = "poweroff"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"  # e.g. GC step when the GC toggle is off


class Run(Base):
    """One execution of a backup/GC/verify/cycle job."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING)

    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)

    # Result summary (populated as the job progresses; nullable while running).
    guests_ok: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    logs: Mapped[list[LogEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="LogEvent.ts",
    )
    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunStep.started_at",
    )
    task_logs: Mapped[list[TaskLogLine]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="TaskLogLine.id",
    )


class RunStep(Base):
    """One stage of a run (wake/wait/backup/gc/poweroff) with its own status + timing.

    Backs the dashboard's per-run step timeline; ``detail`` carries the task UPID while
    running and the error message on failure.
    """

    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=StepStatus.RUNNING)

    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)

    run: Mapped[Run] = relationship(back_populates="steps")


class LogEvent(Base):
    """A single activity-log line, optionally tied to a run."""

    __tablename__ = "log_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), default=None
    )
    ts: Mapped[datetime] = mapped_column(UtcDateTime(), default=_utcnow)
    level: Mapped[str] = mapped_column(String(8), default=LogLevel.INFO)
    message: Mapped[str] = mapped_column(Text)

    run: Mapped[Run | None] = relationship(back_populates="logs")


# Activity log is queried newest-first; index the timestamp for that ordering.
Index("ix_log_events_ts", LogEvent.ts.desc())


class TaskLogLine(Base):
    """One line of raw PVE/PBS task output, captured live while a task runs.

    Backs the dashboard's live "Task log" panel, which narrates the actual vzdump (PVE),
    GC and verify (PBS) output as it streams. Kept separate from :class:`LogEvent` (the
    coarse activity log) so hundreds of raw task lines don't drown it. The rows persist
    after the PBS powers off, so the panel still shows the last power-on session; they age
    out with their parent run via the history-prune cascade.
    """

    __tablename__ = "task_log_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(16))  # StepName value (backup/gc/verify)
    source: Mapped[str] = mapped_column(String(8))  # "pve" | "pbs"
    line_no: Mapped[int] = mapped_column()  # the task's own 1-based line number
    text: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(UtcDateTime(), default=_utcnow)

    run: Mapped[Run] = relationship(back_populates="task_logs")


# The panel tails one run's lines newest-id-last; index by (run_id, id) for that scan.
Index("ix_task_log_lines_run_id", TaskLogLine.run_id, TaskLogLine.id)


class GuestBackup(Base):
    """Cached most-recent backup time per guest, keyed by vmid.

    The dashboard's guest list wants each guest's last backup date, but the PBS is powered
    off most of the time so its snapshots can't be read on demand. The backup cycle upserts
    these rows whenever it has the PBS awake; ``GET /api/guests`` serves the cached values
    so the dashboard shows last-known dates while the PBS sleeps.
    """

    __tablename__ = "guest_backups"

    vmid: Mapped[int] = mapped_column(primary_key=True)
    # The snapshot's own backup time (not when we cached it).
    last_backup: Mapped[datetime] = mapped_column(UtcDateTime())
