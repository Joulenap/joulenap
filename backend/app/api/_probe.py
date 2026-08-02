"""Shared device probes reused by the session-auth status router, the API-key dashboard
router and /metrics: run-history queries plus the per-device reachability +
datastore/load probe.

Every device is probed on every poll, so the probes fan out over a small thread pool: a
sleeping PBS costs a full TCP timeout, and three of them in series would make the status
endpoint feel broken. The timeout stays short for the same reason — dashboards poll, and
a PBS being off is the normal state.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Config, PbsDevice, PveDevice
from ..connectors import net
from ..connectors.errors import ConnectorError
from ..connectors.pbs import DatastoreStatus, NodeLoad, PbsClient
from ..db import session_scope
from ..db.datastore_stats import get_datastore_stat
from ..db.models import Run, RunStatus

# Keep the reachability probe snappy — dashboards poll and a PBS is usually off.
_PBS_PROBE_TIMEOUT = 1.0


def running_run(session: Session) -> Run | None:
    """The currently in-progress run, or None.

    Normally at most one row is RUNNING (the single-run lock), and startup sweeps any
    orphan, so an ordered LIMIT 1 is exact in practice."""
    return session.scalars(
        select(Run)
        .where(Run.status == RunStatus.RUNNING)
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()


def latest_finished_run(session: Session, route_id: str | None = None) -> Run | None:
    """Most recent *finished* run (any terminal status), optionally of one route — so a
    mid-run dashboard shows the previous result instead of an empty one."""
    stmt = (
        select(Run).where(Run.status != RunStatus.RUNNING).order_by(Run.started_at.desc()).limit(1)
    )
    if route_id is not None:
        stmt = stmt.where(Run.route_id == route_id)
    return session.scalars(stmt).first()


class DatastoreView(NamedTuple):
    total: int
    used: int
    used_pct: float


class PbsProbe(NamedTuple):
    """One PBS device's live state, as far as a poll can see it without waking anything."""

    online: bool
    datastore: DatastoreView | None
    load: NodeLoad | None


def _probe_pbs(
    pbs: PbsDevice, connect: Callable[[PbsDevice], PbsClient]
) -> tuple[bool, DatastoreStatus | None, NodeLoad | None]:
    """(online, live datastore, load). Best-effort: a transient PBS/API hiccup yields
    (online, None, None) rather than raising."""
    online = bool(pbs.host) and net.tcp_reachable(pbs.host, pbs.port, _PBS_PROBE_TIMEOUT)
    if not online:
        return False, None, None
    try:
        with connect(pbs) as client:
            return True, client.datastore_status(), client.node_status()
    except ConnectorError:
        return True, None, None


def probe_pbss(
    config: Config, connect: Callable[[PbsDevice], PbsClient]
) -> dict[str, PbsProbe]:
    """Probe every configured PBS concurrently, keyed by device id.

    Datastore usage falls back to the cached row when the box is asleep — which is the
    normal state, and the whole point of Joulenap: the dashboard must still show how full
    the datastore was the last time anyone looked.
    """
    if not config.pbss:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(config.pbss))) as pool:
        raw = list(pool.map(lambda pbs: _probe_pbs(pbs, connect), config.pbss))
    out: dict[str, PbsProbe] = {}
    for pbs, (online, live, load) in zip(config.pbss, raw, strict=True):
        out[pbs.id] = PbsProbe(online, resolve_datastore(pbs, live), load)
    return out


def probe_pves(config: Config) -> dict[str, bool]:
    """Reachability of every configured PVE, keyed by id. A PVE is normally always on, so
    this is a plain TCP probe — no API call, nothing to cache."""
    if not config.pves:
        return {}

    def reachable(pve: PveDevice) -> bool:
        return bool(pve.host) and net.tcp_reachable(pve.host, pve.port, _PBS_PROBE_TIMEOUT)

    with ThreadPoolExecutor(max_workers=min(8, len(config.pves))) as pool:
        return dict(zip([p.id for p in config.pves], pool.map(reachable, config.pves), strict=True))


def resolve_datastore(pbs: PbsDevice, live: DatastoreStatus | None) -> DatastoreView | None:
    """Live-or-cache datastore usage for one device. When ``live`` is present (PBS online)
    persist it and return it; otherwise return the cached row; otherwise None. Opens its own
    transaction, so it is safe to call from a request handler and the values are detached
    (no lazy load)."""
    from ..db.datastore_stats import upsert_datastore_stat

    with session_scope() as session:
        if live is not None:
            upsert_datastore_stat(session, pbs.id, pbs.datastore, live.total, live.used)
            return DatastoreView(live.total, live.used, live.used_pct)
        # Reached both when the PBS is off and when it's reachable but the live datastore
        # read failed (the probe swallowed a ConnectorError) — in the latter case the device
        # still reports online, since that field is reachability-only, while the numbers here
        # fall back to the cache.
        row = get_datastore_stat(session, pbs.id, pbs.datastore)
        if row is None:
            return None
        return DatastoreView(row.total, row.used, row.used_pct)
