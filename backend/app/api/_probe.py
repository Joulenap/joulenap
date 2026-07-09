"""Shared status probes reused by the session-auth status router and the
API-key dashboard router: the 'last backup cycle' query and the PBS
reachability + datastore/load probe."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Config
from ..connectors import net
from ..connectors.errors import ConnectorError
from ..connectors.pbs import DatastoreStatus, NodeLoad, PbsClient
from ..db import session_scope
from ..db.datastore_stats import get_datastore_stat, upsert_datastore_stat
from ..db.models import Run, RunKind, RunStatus

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


def latest_finished_cycle_run(session: Session) -> Run | None:
    """Most recent backup cycle that has finished (any terminal status), ignoring an
    in-progress RUNNING cycle — so a mid-backup dashboard shows the previous result."""
    return session.scalars(
        select(Run)
        .where(Run.kind == RunKind.CYCLE, Run.status != RunStatus.RUNNING)
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


class DatastoreView(NamedTuple):
    total: int
    used: int
    used_pct: float


def resolve_datastore(datastore: str, live: DatastoreStatus | None) -> DatastoreView | None:
    """Live-or-cache datastore usage. When ``live`` is present (PBS online) persist it and
    return it; otherwise return the cached row; otherwise None. Opens its own transaction, so
    it is safe to call from a request handler and the values are detached (no lazy load)."""
    with session_scope() as session:
        if live is not None:
            upsert_datastore_stat(session, datastore, live.total, live.used)
            return DatastoreView(live.total, live.used, live.used_pct)
        # Reached both when the PBS is off and when it's reachable but the live
        # datastore read failed (probe_pbs swallowed a ConnectorError) — in the
        # latter case pbs_state still reports "online" since that field is
        # reachability-only, while the numbers here fall back to the cache.
        row = get_datastore_stat(session, datastore)
        if row is None:
            return None
        return DatastoreView(row.total, row.used, row.used_pct)
