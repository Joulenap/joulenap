"""POST /api/runs/{id}/stop — ask the in-flight run to stop (11.2).

Starting a run lives with what it runs: /api/routes/{id}/run for a route, and
/api/devices/pbss/{id}/gc|verify for ad-hoc maintenance. Stopping is about the *run*, so it
hangs off the run itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..db import session_scope
from ..db.models import Run, RunStatus
from ..db.startup import sweep_orphaned_runs
from .deps import JobService, get_job_service, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["jobs"])


class StopRequest(BaseModel):
    # Power the PBS off after stopping (the toggle in the stop dialog). Default: leave it
    # on, since cancelling often means the user wants to work on the box.
    power_off: bool = False


@router.post("/runs/{run_id}/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_run(
    run_id: int,
    body: StopRequest | None = None,
    job_service: JobService = Depends(get_job_service),
) -> dict[str, int]:
    """202 means the request was accepted, not that the run has ended: cancellation is
    cooperative, so the worker stops the remote vzdump/GC task and unwinds within a poll
    interval. Poll GET /api/runs/{id} for the final status.

    The run id is in the path rather than "stop whatever is running" on purpose: a click
    landing as one run ends and the next begins must not stop the wrong job.
    """
    if job_service.cancel(run_id, power_off=(body or StopRequest()).power_off):
        return {"run_id": run_id}
    # Not in flight — but is the history still showing it RUNNING? Then its worker died
    # without closing it out (an unwritable DB at the wrong moment, #38) and nothing but a
    # restart's sweep would ever end it. Stop is what the user reaches for; let it do the
    # sweep for that one run instead of answering "not in progress" to a row that says it is.
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is not None and run.status == RunStatus.RUNNING:
            sweep_orphaned_runs(session, run_ids=[run_id])
            return {"run_id": run_id}
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="That run is not the one currently in progress",
    )
