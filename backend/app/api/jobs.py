"""POST /api/backup/run and /api/gc/run — kick off a run now (returns immediately).

Both return 202 with the new run id; poll GET /api/runs/{id} for progress. The single-run
guard means a second request while one is in flight gets 409 Conflict.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..db.models import RunTrigger
from ..jobs import AlreadyRunningError
from .deps import JobService, get_job_service, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["jobs"])


class RunStarted(BaseModel):
    run_id: int


def _start(submit) -> RunStarted:
    try:
        run_id = submit(RunTrigger.MANUAL)
    except AlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RunStarted(run_id=run_id)


@router.post("/backup/run", response_model=RunStarted, status_code=status.HTTP_202_ACCEPTED)
def run_backup(job_service: JobService = Depends(get_job_service)) -> RunStarted:
    return _start(job_service.submit_backup)


@router.post("/gc/run", response_model=RunStarted, status_code=status.HTTP_202_ACCEPTED)
def run_gc(job_service: JobService = Depends(get_job_service)) -> RunStarted:
    return _start(job_service.submit_gc)
