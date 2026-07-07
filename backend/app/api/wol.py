"""POST /api/wol/test — send a one-off Wake-on-LAN packet to verify the MAC/network."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..connectors.errors import WolError
from ..core.config_store import ConfigStore
from .deps import JobService, get_config_store, get_job_service, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["wol"])


class WolResult(BaseModel):
    sent: bool
    mac: str


@router.post("/wol/test", response_model=WolResult)
def wol_test(
    store: ConfigStore = Depends(get_config_store),
    job_service: JobService = Depends(get_job_service),
) -> WolResult:
    config = store.config
    if not config.pbs.mac:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No PBS MAC address configured"
        )
    try:
        job_service.deps.send_wol(config)
    except WolError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return WolResult(sent=True, mac=config.pbs.mac)
