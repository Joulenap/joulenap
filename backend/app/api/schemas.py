"""Response models shared across the status / logs / runs routers."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..db.models import LogEvent, Run, RunStep, TaskLogLine
from ..notify.messages import render_error


def _error_params(run: Run) -> dict[str, object] | None:
    """``run.error_params`` decoded, or ``None`` if it is absent or unreadable.

    Never raises: the column is free-form JSON written by an older version of the app, and a
    history page must not 500 because one row's payload is malformed. ``render_error`` falls
    back to the stored English text in that case.
    """
    if not run.error_params:
        return None
    try:
        params = json.loads(run.error_params)
    except ValueError:
        return None
    return params if isinstance(params, dict) else None


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    # The route this run came from, for the history's route column. Both null for an ad-hoc
    # PBS maintenance run; ``route_name`` is denormalised on the row, so a deleted route's
    # history still reads correctly.
    route_id: str | None = None
    route_name: str | None = None
    # Nullable while a run is in flight, and for kinds that don't touch guests (GC/verify).
    guests_ok: int | None = None
    #: Already rendered in ``app.language`` — the UI shows this string as-is. The row stores
    #: ``error_key``/``error_params``; ``error`` (English) is the fallback for pre-1.0 rows
    #: and for text that came from someone else's software.
    error: str | None = None

    @classmethod
    def of(cls, run: Run, language: str = "en") -> RunSummary:
        summary = cls.model_validate(run)
        summary.error = render_error(language, run.error_key, _error_params(run), run.error)
        return summary


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
    def of(cls, run: Run, language: str = "en") -> RunDetail:
        return cls(
            **RunSummary.of(run, language).model_dump(),
            steps=[StepInfo.of(s) for s in run.steps],
            logs=[LogLine.of(e) for e in run.logs],
        )
