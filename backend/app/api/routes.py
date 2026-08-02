"""CRUD for routes, plus "run this one now".

A route is "sources -> target + schedule" and is the only thing that ever wakes a PBS, so
this is the endpoint the homepage's route strip and its editor modal are built on. Routes
hold no secrets, so they are returned verbatim — the redaction dance is only needed for
devices.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import Route
from ..core.config_store import ConfigStore
from ..db.models import RunTrigger
from ..jobs import AlreadyRunningError
from ._config_edit import save_section
from .deps import (
    JobService,
    Scheduler,
    get_config_store,
    get_job_service,
    get_scheduler,
    require_auth,
)

router = APIRouter(prefix="/routes", dependencies=[Depends(require_auth)], tags=["routes"])


def _dump(config) -> list[dict[str, Any]]:
    return [r.model_dump(mode="python") for r in config.routes]


def _index(config, route_id: str) -> int:
    for i, route in enumerate(config.routes):
        if route.id == route_id:
            return i
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"No route '{route_id}'"
    )


@router.get("")
def list_routes(store: ConfigStore = Depends(get_config_store)) -> list[dict[str, Any]]:
    return _dump(store.config)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_route(
    route: Route,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    if any(r.id == route.id for r in store.config.routes):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Route '{route.id}' already exists"
        )
    routes = [*_dump(store.config), route.model_dump(mode="python")]
    save_section(store, scheduler, "routes", routes)
    return route.model_dump(mode="python")


@router.put("/{route_id}")
def update_route(
    route_id: str,
    route: Route,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    """Replace one route. The body's ``id`` wins — renaming a route is a delete + create as
    far as history is concerned (past runs keep the old id and their denormalised name)."""
    index = _index(store.config, route_id)
    routes = _dump(store.config)
    routes[index] = route.model_dump(mode="python")
    save_section(store, scheduler, "routes", routes)
    return route.model_dump(mode="python")


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_route(
    route_id: str,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> None:
    """Delete a route. No guard: nothing references a route, and its run history survives
    (``runs.route_name`` is denormalised precisely so old runs still read correctly)."""
    index = _index(store.config, route_id)
    routes = _dump(store.config)
    del routes[index]
    save_section(store, scheduler, "routes", routes)


class RunOptions(BaseModel):
    # Leave the PBS awake after the run instead of powering it off (manual convenience,
    # e.g. the box was woken for a restore). Scheduled runs never use this.
    keep_on: bool = False


class RunQueued(BaseModel):
    route_id: str
    #: How many runs are ahead of this one; 0 means it starts immediately.
    queued: int


@router.post("/{route_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_route_now(
    route_id: str,
    opts: RunOptions | None = None,
    job_service: JobService = Depends(get_job_service),
) -> RunQueued:
    """Queue a manual run. 202 means accepted, not finished — poll ``GET /api/status``.

    Runs execute one at a time, so a second route requested now simply waits its turn
    instead of being rejected; only re-running the *same* route conflicts.
    """
    try:
        ahead = job_service.run_route(
            route_id, RunTrigger.MANUAL, power_off=not (opts or RunOptions()).keep_on
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No route '{route_id}'"
        ) from exc
    except AlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RunQueued(route_id=route_id, queued=ahead)
