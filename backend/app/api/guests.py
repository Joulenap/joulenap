"""GET /api/guests — list CTs/VMs from PVE for the selection panel."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..connectors.errors import ConnectorError
from ..core.config_store import ConfigStore
from ..db import get_session
from ..db.guest_backups import get_last_backups
from .deps import get_config_store, get_job_service, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["guests"])


class GuestInfo(BaseModel):
    vmid: int
    name: str
    type: str  # "qemu" | "lxc"
    status: str
    # Most recent backup time, from the cache refreshed while the PBS was last awake.
    # null when the guest has never been backed up (or before the first cycle runs).
    last_backup: datetime | None = None


@router.get("/guests", response_model=list[GuestInfo])
def list_guests(
    store: ConfigStore = Depends(get_config_store),
    job_service=Depends(get_job_service),
    session: Session = Depends(get_session),
) -> list[GuestInfo]:
    try:
        with job_service.deps.build_pve(store.config) as pve:
            guests = pve.list_guests()
    except ConnectorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not reach PVE: {exc}"
        ) from exc
    last = get_last_backups(session, [g.vmid for g in guests])
    return [
        GuestInfo(
            vmid=g.vmid, name=g.name, type=g.type, status=g.status, last_backup=last.get(g.vmid)
        )
        for g in guests
    ]
