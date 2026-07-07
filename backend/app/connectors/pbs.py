"""Proxmox Backup Server API client.

The backup cycle uses this to read datastore status and run garbage collection while
the PBS is awake. PBS speaks the same /api2/json dialect as PVE but with a different
token header format (``PBSAPIToken=id:secret``) and its own endpoints. The standalone
:func:`get_fingerprint` helper backs the wizard's fingerprint auto-detection.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ._http import ProxmoxApiClient
from ._tasks import LogLine, poll_task
from .errors import ApiError


@dataclass
class DatastoreStatus:
    total: int  # bytes
    used: int
    avail: int

    @property
    def used_pct(self) -> float:
        return round(self.used / self.total * 100, 1) if self.total else 0.0

    @property
    def avail_pct(self) -> float:
        return round(self.avail / self.total * 100, 1) if self.total else 0.0


@dataclass
class NodeLoad:
    """Live PBS node stats for the dashboard load tile."""

    cpu: int  # percent 0-100
    mem: int  # percent 0-100
    uptime: int  # seconds since the PBS booted (i.e. how long it's been awake)


class PbsClient:
    def __init__(
        self,
        host: str,
        datastore: str,
        token_id: str,
        token_secret: str,
        port: int = 8007,
        node: str = "localhost",
        verify: bool = False,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.datastore = datastore
        self.node = node
        self._api = ProxmoxApiClient(
            base_url=f"https://{host}:{port}/api2/json",
            auth_header=f"PBSAPIToken={token_id}:{token_secret}",
            verify=verify,
            timeout=timeout,
            transport=transport,
        )

    def version(self) -> dict[str, Any]:
        return self._api.request("GET", "/version")

    def datastore_status(self) -> DatastoreStatus:
        data = self._api.request("GET", f"/admin/datastore/{self.datastore}/status")
        if not data:
            # PBS answered ``{"data": null}`` (e.g. datastore not yet online) — degrade to a
            # ConnectorError so /api/status shows "—" rather than 500ing on ``data["total"]``.
            raise ApiError(f"No status returned for datastore {self.datastore!r}")
        return DatastoreStatus(
            total=int(data["total"]),
            used=int(data["used"]),
            avail=int(data.get("avail", data["total"] - data["used"])),
        )

    def node_status(self) -> NodeLoad:
        """Live CPU %, memory %, and uptime for the PBS node.

        PBS reports ``cpu`` as a 0-1 fraction and memory as bytes (normalised to whole
        percentages); ``uptime`` is seconds since boot — for a normally-off PBS that's how
        long it has been awake this cycle.
        """
        data = self._api.request("GET", f"/nodes/{self.node}/status")
        if not data:
            raise ApiError(f"No node status returned for {self.node!r}")
        mem = data.get("memory") or {}
        mem_total = int(mem.get("total", 0))
        mem_used = int(mem.get("used", 0))
        mem_pct = round(mem_used / mem_total * 100) if mem_total else 0
        return NodeLoad(
            cpu=round(float(data.get("cpu", 0.0)) * 100),
            mem=mem_pct,
            uptime=int(data.get("uptime", 0)),
        )

    # --- snapshots -----------------------------------------------------------

    def latest_backups(self) -> dict[int, int]:
        """Map each guest's vmid -> epoch seconds of its most recent snapshot.

        One call lists every snapshot in the datastore; we keep the max ``backup-time`` per
        ``backup-id``. Only ``vm``/``ct`` snapshots with a numeric id are returned (PBS also
        stores ``host`` backups, which aren't PVE guests). Used to refresh the dashboard's
        last-backup cache while the PBS is awake.
        """
        snaps = self._api.request("GET", f"/admin/datastore/{self.datastore}/snapshots") or []
        latest: dict[int, int] = {}
        for snap in snaps:
            if snap.get("backup-type") not in ("vm", "ct"):
                continue
            try:
                vmid = int(snap.get("backup-id"))
            except (TypeError, ValueError):
                continue
            ts = int(snap.get("backup-time", 0))
            if ts > latest.get(vmid, 0):
                latest[vmid] = ts
        return latest

    # --- running tasks (pre-power-off guard) ---------------------------------

    def active_tasks(self) -> list[dict[str, Any]]:
        """The PBS node's currently-running tasks (UPID/type/...).

        Used by the pre-power-off guard so a clean shutdown never interrupts a task we
        didn't start (a manual GC/verify/prune, another backup, a sync). Needs the token's
        ``Sys.Audit`` on ``/system`` (the wizard grants this).
        """
        tasks = self._api.request("GET", f"/nodes/{self.node}/tasks", params={"running": 1})
        return tasks or []

    def wait_until_idle(
        self,
        timeout: float,
        interval: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> bool:
        """Poll until no task is running, or ``timeout`` elapses. True => safe to power off."""
        deadline = time.monotonic() + timeout
        while True:
            if not self.active_tasks():
                return True
            if time.monotonic() >= deadline:
                return False
            sleep(interval)

    # --- garbage collection --------------------------------------------------

    def start_gc(self) -> str:
        """Start garbage collection on the datastore; returns the task UPID."""
        return self._api.request("POST", f"/admin/datastore/{self.datastore}/gc")

    # --- verification --------------------------------------------------------

    def start_verify(
        self, *, ignore_verified: bool = True, outdated_after: int | None = None
    ) -> str:
        """Start a verification task on the datastore; returns the task UPID.

        Verification re-reads stored snapshots and re-checks their chunk checksums to catch
        on-disk corruption — a PBS-side, read-only integrity check (the source guests are
        not touched). ``ignore_verified`` skips snapshots already verified and not yet
        outdated, so routine runs only re-read new/stale data; ``outdated_after`` is the
        re-verify window in days (omit to only ever verify never-verified snapshots).
        """
        data: dict[str, Any] = {"ignore-verified": 1 if ignore_verified else 0}
        if ignore_verified and outdated_after is not None:
            data["outdated-after"] = outdated_after
        return self._api.request(
            "POST", f"/admin/datastore/{self.datastore}/verify", data=data
        )

    def task_status(self, upid: str) -> dict[str, Any]:
        return self._api.request("GET", f"/nodes/{self.node}/tasks/{upid}/status")

    def task_log(self, upid: str, start: int = 0, limit: int = 5000) -> list[LogLine]:
        """Fetch task-log lines starting at offset ``start``, as ``(line_no, text)`` pairs.

        ``start`` skips that many lines, so the returned lines are numbered ``start+1..``;
        used by :meth:`wait_task` to tail a running GC/verify task for the live log panel.
        """
        data = self._api.request(
            "GET",
            f"/nodes/{self.node}/tasks/{upid}/log",
            params={"start": start, "limit": limit},
        )
        return [(int(e["n"]), e.get("t") or "") for e in (data or [])]

    def wait_task(
        self,
        upid: str,
        poll_interval: float = 5.0,
        timeout: float = 6 * 3600,
        sleep: Callable[[float], None] = time.sleep,
        *,
        on_log: Callable[[list[LogLine]], None] | None = None,
    ) -> dict[str, Any]:
        """Poll a task until it stops. Returns the final status; raises on non-OK exit.

        Pass ``on_log`` to also tail the task log — each new batch of ``(line_no, text)``
        pairs is handed to it as the task runs.
        """
        log_fn = (lambda start: self.task_log(upid, start)) if on_log else None
        return poll_task(
            self.task_status, upid, poll_interval, timeout, sleep,
            log_fn=log_fn, on_lines=on_log,
        )

    def close(self) -> None:
        self._api.close()

    def __enter__(self) -> PbsClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def get_fingerprint(host: str, port: int = 8007, timeout: float = 5.0) -> str:
    """Return the PBS TLS cert's SHA-256 fingerprint as colon-separated hex.

    Connects without verifying (the cert is typically self-signed) and hashes the
    presented certificate — the same value PBS shows as "Fingerprint" and that the
    wizard pins. Raises :class:`ApiError` if the cert can't be retrieved.
    """
    from .tls import fetch_peer_der, fingerprint_hex  # local import avoids a cycle at top

    return fingerprint_hex(fetch_peer_der(host, port, timeout))
