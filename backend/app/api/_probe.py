"""Shared status probes reused by the session-auth status router and the
API-key dashboard router: the 'last backup cycle' query and the PBS
reachability + datastore/load probe."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Config
from ..connectors import net
from ..connectors.errors import ConnectorError
from ..connectors.pbs import DatastoreStatus, NodeLoad, PbsClient
from ..db.models import Run, RunKind

# Keep the reachability probe snappy — dashboards poll and the PBS is usually off.
_PBS_PROBE_TIMEOUT = 1.0


def latest_cycle_run(session: Session) -> Run | None:
    """Most recent backup *cycle* (manual or scheduled). Filtered to CYCLE so a
    standalone manual GC run doesn't masquerade as the last backup."""
    return session.scalars(
        select(Run)
        .where(Run.kind == RunKind.CYCLE)
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()


def probe_pbs(
    config: Config,
    build_pbs: Callable[[Config], PbsClient],
) -> tuple[bool, DatastoreStatus | None, NodeLoad | None]:
    """Return (pbs_online, datastore, load). Best-effort: a transient PBS/API
    hiccup yields (online, None, None) rather than raising."""
    pbs = config.pbs
    online = bool(pbs.host) and net.tcp_reachable(pbs.host, pbs.port, _PBS_PROBE_TIMEOUT)
    datastore: DatastoreStatus | None = None
    load: NodeLoad | None = None
    if online:
        try:
            with build_pbs(config) as client:
                datastore = client.datastore_status()
                load = client.node_status()
        except ConnectorError:
            pass
    return online, datastore, load
