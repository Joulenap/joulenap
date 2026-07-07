"""Proxmox VE API client.

Covers what the backup cycle and the guest-selection panel need: list guests,
trigger ``vzdump`` to the PBS storage, and poll the resulting task to completion.
Token auto-provisioning (root ticket auth) belongs to the setup wizard (milestone 5).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ._http import ProxmoxApiClient
from ._tasks import LogLine, poll_task

# vzdump prune-backups keys, in the order PVE expects them.
_RETENTION_KEYS = ("keep_last", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly")


@dataclass
class Guest:
    vmid: int
    name: str
    type: str  # "qemu" (VM) or "lxc" (CT)
    status: str  # "running" | "stopped" | ...

    @property
    def is_ct(self) -> bool:
        return self.type == "lxc"


def build_prune_string(retention: dict[str, int]) -> str | None:
    """Turn a retention dict (keep_daily=7, …) into a vzdump ``prune-backups`` string.

    Returns ``None`` if nothing is set, so vzdump falls back to the storage default
    rather than pruning everything.
    """
    parts = []
    for key in _RETENTION_KEYS:
        value = retention.get(key, 0)
        if value:
            parts.append(f"{key.replace('_', '-')}={value}")
    return ",".join(parts) if parts else None


class PveClient:
    def __init__(
        self,
        host: str,
        node: str,
        token_id: str,
        token_secret: str,
        port: int = 8006,
        verify_tls: bool = False,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.node = node
        self._api = ProxmoxApiClient(
            base_url=f"https://{host}:{port}/api2/json",
            auth_header=f"PVEAPIToken={token_id}={token_secret}",
            verify=verify_tls,
            timeout=timeout,
            transport=transport,
        )

    # --- discovery -----------------------------------------------------------

    def version(self) -> dict[str, Any]:
        return self._api.request("GET", "/version")

    def list_storages(self) -> list[dict[str, Any]]:
        return self._api.request("GET", f"/nodes/{self.node}/storage") or []

    def list_nodes(self) -> list[dict[str, Any]]:
        """Cluster nodes (``[{node, status, ...}]``) — used by the wizard's node dropdown."""
        return self._api.request("GET", "/nodes") or []

    def list_pbs_storages(self) -> list[dict[str, Any]]:
        """Cluster storages of ``type=pbs`` with their full config.

        The datacenter-level ``/storage`` endpoint returns the configuration (server,
        datastore, fingerprint) — unlike the per-node endpoint which only reports status —
        so the wizard can derive the whole PBS connection from the selected storage.
        """
        storages = self._api.request("GET", "/storage", params={"type": "pbs"}) or []
        return storages

    def get_storage(self, storage_id: str) -> dict[str, Any]:
        """Full config of one storage (``server``, ``datastore``, ``fingerprint``, …)."""
        return self._api.request("GET", f"/storage/{storage_id}")

    def list_guests(self) -> list[Guest]:
        """All VMs (qemu) and containers (lxc) on the node, sorted by vmid."""
        guests: list[Guest] = []
        for kind in ("qemu", "lxc"):
            for g in self._api.request("GET", f"/nodes/{self.node}/{kind}") or []:
                guests.append(
                    Guest(
                        vmid=int(g["vmid"]),
                        name=g.get("name") or f"{kind}-{g['vmid']}",
                        type=kind,
                        status=g.get("status", "unknown"),
                    )
                )
        guests.sort(key=lambda g: g.vmid)
        return guests

    # --- backup --------------------------------------------------------------

    def vzdump(
        self,
        storage: str,
        *,
        vmids: list[int] | None = None,
        all_guests: bool = False,
        mode: str = "snapshot",
        prune_backups: str | None = None,
        bwlimit: int = 0,
    ) -> str:
        """Start a vzdump backup; returns the task UPID to poll with :meth:`wait_task`.

        Either pass ``vmids`` (explicit selection) or ``all_guests=True``.
        """
        params: dict[str, Any] = {"storage": storage, "mode": mode}
        if all_guests:
            params["all"] = 1
        elif vmids:
            params["vmid"] = ",".join(str(v) for v in vmids)
        if prune_backups:
            params["prune-backups"] = prune_backups
        if bwlimit:
            params["bwlimit"] = bwlimit
        return self._api.request("POST", f"/nodes/{self.node}/vzdump", data=params)

    # --- tasks ---------------------------------------------------------------

    def task_status(self, upid: str) -> dict[str, Any]:
        return self._api.request("GET", f"/nodes/{self.node}/tasks/{upid}/status")

    def task_log(self, upid: str, start: int = 0, limit: int = 5000) -> list[LogLine]:
        """Fetch task-log lines starting at offset ``start``, as ``(line_no, text)`` pairs.

        ``start`` skips that many lines, so the returned lines are numbered ``start+1..``;
        used by :meth:`wait_task` to tail a running task for the live task-log panel.
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
        poll_interval: float = 3.0,
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

    def __enter__(self) -> PveClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
