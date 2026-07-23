"""Response models shared across the status / logs / runs routers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..db.models import LogEvent, Run, RunStep, TaskLogLine


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    # Nullable while a run is in flight, and for kinds that don't touch guests (GC/verify).
    guests_ok: int | None = None
    error: str | None = None

    @classmethod
    def of(cls, run: Run) -> RunSummary:
        return cls.model_validate(run)


class LogLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None
    ts: datetime
    level: str
    message: str

    @classmethod
    def of(cls, event: LogEvent) -> LogLine:
        return cls.model_validate(event)


class StepInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    detail: str | None = None

    @classmethod
    def of(cls, step: RunStep) -> StepInfo:
        return cls.model_validate(step)


class TaskLogLineSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step: str
    source: str
    text: str
    ts: datetime

    @classmethod
    def of(cls, line: TaskLogLine) -> TaskLogLineSchema:
        return cls.model_validate(line)


class TaskLogResponse(BaseModel):
    """A window of the live task log: the run it belongs to plus its new lines.

    ``run_id`` identifies the session so the client can reset its buffer when a new run
    starts (line ids are globally increasing, so the client just polls ``after`` its last
    id). ``null`` when no task has ever logged.
    """

    run_id: int | None
    lines: list[TaskLogLineSchema]


class RunDetail(RunSummary):
    steps: list[StepInfo]
    logs: list[LogLine]

    @classmethod
    def of(cls, run: Run) -> RunDetail:
        return cls(
            **RunSummary.of(run).model_dump(),
            steps=[StepInfo.of(s) for s in run.steps],
            logs=[LogLine.of(e) for e in run.logs],
        )
