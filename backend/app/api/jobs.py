"""POST /api/backup/run and /api/gc/run — kick off a run now (returns immediately).

Both return 202 with the new run id; poll GET /api/runs/{id} for progress. The single-run
guard means a second request while one is in flight gets 409 Conflict. An optional
``{"keep_on": true}`` body leaves the PBS powered on after the job (default: power it off).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..db.models import RunTrigger
from ..jobs import AlreadyRunningError
from .deps import JobService, get_job_service, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["jobs"])


class RunOptions(BaseModel):
    # Leave the PBS awake after the job instead of powering it off (manual convenience,
    # e.g. the box was woken for a restore). Scheduled runs never use this.
    keep_on: bool = False


class RunStarted(BaseModel):
    run_id: int


def _start(submit, keep_on: bool) -> RunStarted:
    try:
        run_id = submit(RunTrigger.MANUAL, power_off=not keep_on)
    except AlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RunStarted(run_id=run_id)


@router.post("/backup/run", response_model=RunStarted, status_code=status.HTTP_202_ACCEPTED)
def run_backup(
    opts: RunOptions | None = None, job_service: JobService = Depends(get_job_service)
) -> RunStarted:
    return _start(job_service.submit_backup, (opts or RunOptions()).keep_on)


@router.post("/gc/run", response_model=RunStarted, status_code=status.HTTP_202_ACCEPTED)
def run_gc(
    opts: RunOptions | None = None, job_service: JobService = Depends(get_job_service)
) -> RunStarted:
    return _start(job_service.submit_gc, (opts or RunOptions()).keep_on)
