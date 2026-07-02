"""POST /api/power/on and /api/power/off — manual PBS power control.

Power-on sends a Wake-on-LAN packet; power-off issues the SSH poweroff. These are the
dashboard's manual power buttons (the backup cycle manages power automatically on its
own). Power-off is refused while a backup/GC run is in flight.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..connectors.errors import ConnectorError
from ..core.config_store import ConfigStore
from .deps import get_config_store, get_job_service, require_auth

router = APIRouter(prefix="/power", dependencies=[Depends(require_auth)], tags=["power"])


class PowerResult(BaseModel):
    ok: bool


@router.post("/on", response_model=PowerResult)
def power_on(
    store: ConfigStore = Depends(get_config_store),
    job_service=Depends(get_job_service),
) -> PowerResult:
    config = store.config
    if not config.pbs.mac:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No PBS MAC address configured"
        )
    try:
        job_service.deps.send_wol(config)
    except ConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PowerResult(ok=True)


@router.post("/off", response_model=PowerResult)
def power_off(
    store: ConfigStore = Depends(get_config_store),
    job_service=Depends(get_job_service),
) -> PowerResult:
    if job_service.is_running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A backup or GC run is in progress; cannot power off the PBS",
        )
    try:
        job_service.deps.build_power(store.config).poweroff()
    except ConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PowerResult(ok=True)
