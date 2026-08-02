"""POST /api/runs/{id}/stop — ask the in-flight run to stop (11.2).

Starting a run lives with what it runs: /api/routes/{id}/run for a route, and
/api/devices/pbss/{id}/gc|verify for ad-hoc maintenance. Stopping is about the *run*, so it
hangs off the run itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

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
    if not job_service.cancel(run_id, power_off=(body or StopRequest()).power_off):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That run is not the one currently in progress",
        )
    return {"run_id": run_id}
