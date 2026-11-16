"""GET /api/guests and /api/nodes: one PVE's CTs/VMs, and the cluster nodes behind it.

Scoped to a single PVE by query parameter: vmids collide across PVEs, so a flat list could
not say which "100" it meant — which is also why a route stores its guest selection per
source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..connectors.errors import ConnectorError
from ..core.config_store import ConfigStore
from ..db import get_session
from ..db.guest_backups import list_last_backups
from .deps import JobService, get_config_store, get_job_service, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["guests"])


class GuestInfo(BaseModel):
    vmid: int
    name: str
    type: str  # "qemu" | "lxc"
    status: str
    #: Cluster node holding this guest. Always set — a standalone PVE is a one-node cluster
    #: as far as the API is concerned.
    node: str
    # Most recent backup time, from the cache refreshed while the PBS was last awake.
    # null when the guest has never been backed up (or before the first run).
    last_backup: datetime | None = None
    #: Which backup servers hold a backup of this guest, sorted. More than one when a sync
    #: route copied it on: the homepage's guest panel shows them as chips.
    pbs_ids: list[str] = []


class NodeInfo(BaseModel):
    node: str
    status: str  # "online" | "offline" | "unknown"


def _ask_pve(store: ConfigStore, job_service: JobService, pve: str, call: Any) -> Any:
    device = next((p for p in store.config.pves if p.id == pve), None)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No pve '{pve}'"
        )
    try:
        with job_service.deps.connect_pve(device) as client:
            return call(client)
    except ConnectorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not reach PVE: {exc}"
        ) from exc


@router.get("/nodes", response_model=list[NodeInfo])
def list_nodes(
    pve: str = Query(description="Which configured PVE to list"),
    store: ConfigStore = Depends(get_config_store),
    job_service: JobService = Depends(get_job_service),
) -> list[NodeInfo]:
    """Every node of the cluster, guests or not.

    The node count used to be read off the guest listing, which misses a node that holds
    no guests (or only templates): a 4-node cluster read as 3 (issue #94).
    """
    rows = _ask_pve(store, job_service, pve, lambda c: c.list_nodes())
    return sorted(
        (NodeInfo(node=r.get("node", ""), status=r.get("status") or "unknown") for r in rows),
        key=lambda n: n.node,
    )


@router.get("/guests", response_model=list[GuestInfo])
def list_guests(
    pve: str = Query(description="Which configured PVE to list"),
    store: ConfigStore = Depends(get_config_store),
    job_service: JobService = Depends(get_job_service),
    session: Session = Depends(get_session),
) -> list[GuestInfo]:
    guests = _ask_pve(store, job_service, pve, lambda c: c.list_cluster_guests())
    # Per-(pve, pbs) rows, not the collapsed newest-per-vmid view: a vmid can exist on two
    # PVEs, and the panel names the PBSs holding each backup.
    cached: dict[int, list[tuple[str, datetime]]] = {}
    for row in list_last_backups(session, pve_id=pve):
        cached.setdefault(row.vmid, []).append((row.pbs_id, row.last_backup))
    return [
        GuestInfo(
            vmid=g.vmid,
            name=g.name,
            type=g.type,
            status=g.status,
            node=g.node,
            last_backup=max((t for _, t in cached.get(g.vmid, [])), default=None),
            pbs_ids=sorted(pbs_id for pbs_id, _ in cached.get(g.vmid, [])),
        )
        for g in guests
    ]
