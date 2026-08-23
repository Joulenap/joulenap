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
    # Which cluster node holds it. Empty from the per-node listing (the caller knows the
    # node already); filled in by :meth:`PveClient.list_cluster_guests`, which is what lets
    # a route group its vzdump calls per node.
    node: str = ""

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
        token_id: str,
        token_secret: str,
        # A route's PVE device has no node (nodes are discovered at runtime): the node-scoped
        # endpoints below are the 0.9 path and the wizard's, and the task endpoints read the
        # node from the UPID instead.
        node: str = "",
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

    def list_cluster_guests(self) -> list[Guest]:
        """Every VM/CT this endpoint knows about, each tagged with the node holding it.

        One call covers a whole cluster — the endpoint proxies its nodes — and works just as
        well against a standalone node, which the API simply reports as a one-node cluster.
        Templates are dropped: they are never backed up, so counting them would inflate the
        guest tally and naming one explicitly would fail the vzdump task.
        """
        rows = self._api.request("GET", "/cluster/resources", params={"type": "vm"}) or []
        guests: list[Guest] = []
        for r in rows:
            if r.get("template") or r.get("vmid") is None:
                continue
            kind = r.get("type") or ""
            guests.append(
                Guest(
                    vmid=int(r["vmid"]),
                    name=r.get("name") or f"{kind}-{r['vmid']}",
                    type=kind,
                    status=r.get("status", "unknown"),
                    node=r.get("node", ""),
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
        exclude: list[int] | None = None,
        mode: str = "snapshot",
        prune_backups: str | None = None,
        bwlimit: int = 0,
        node: str = "",
    ) -> str:
        """Start a vzdump backup; returns the task UPID to poll with :meth:`wait_task`.

        Either pass ``vmids`` (explicit selection) or ``all_guests=True``. ``exclude`` narrows
        an ``all_guests`` run down and is ignored without it, which is the PVE API's own rule
        (``--exclude`` assumes ``--all``). ``node`` picks the cluster node to run it on (a
        backup route starts one task per node); it defaults to the client's own node.
        """
        # PVE fills the backup's Notes only when asked to: the GUI's backup-job form defaults
        # to "{{guestname}}", a bare API call defaults to nothing. Send it ourselves so a
        # Joulenap-triggered backup is labelled with the guest name in the PVE/PBS lists like
        # a native job. Available since libpve-guest-common 4.1-2 (PVE 7.3).
        params: dict[str, Any] = {
            "storage": storage,
            "mode": mode,
            "notes-template": "{{guestname}}",
        }
        if all_guests:
            params["all"] = 1
            if exclude:
                params["exclude"] = ",".join(str(v) for v in exclude)
        elif vmids:
            params["vmid"] = ",".join(str(v) for v in vmids)
        if prune_backups:
            params["prune-backups"] = prune_backups
        if bwlimit:
            params["bwlimit"] = bwlimit
        return self._api.request("POST", f"/nodes/{node or self.node}/vzdump", data=params)

    # --- tasks ---------------------------------------------------------------

    def _task_node(self, upid: str) -> str:
        """The node a task runs on, read from its own UPID (``UPID:<node>:<pid>:…``).

        One client can drive tasks on several nodes (a cluster route starts one vzdump per
        node), so the node has to come from the task rather than from the client. Falls back
        to the client's node if the string isn't a UPID.
        """
        parts = upid.split(":")
        if len(parts) > 2 and parts[0] == "UPID" and parts[1]:
            return parts[1]
        return self.node

    def task_status(self, upid: str) -> dict[str, Any]:
        return self._api.request("GET", f"/nodes/{self._task_node(upid)}/tasks/{upid}/status")

    def task_log(self, upid: str, start: int = 0, limit: int = 5000) -> list[LogLine]:
        """Fetch task-log lines starting at offset ``start``, as ``(line_no, text)`` pairs.

        ``start`` skips that many lines, so the returned lines are numbered ``start+1..``;
        used by :meth:`wait_task` to tail a running task for the live task-log panel.
        """
        data = self._api.request(
            "GET",
            f"/nodes/{self._task_node(upid)}/tasks/{upid}/log",
            params={"start": start, "limit": limit},
        )
        lines = [(int(e["n"]), e.get("t") or "") for e in (data or [])]
        # A task whose log file is still empty answers with one placeholder line, "no
        # content", numbered 1. Storing it would advance the tailer's offset past the real
        # line 1 ("INFO: starting new backup job: ...") once vzdump starts writing.
        if lines == [(1, "no content")]:
            return []
        return lines

    def stop_task(self, upid: str) -> None:
        """Ask PVE to stop a running task (the vzdump behind a cancelled run).

        Without this a cancelled backup would keep running on the PVE host after Joulenap
        stopped watching it, and the next run would collide with it.
        """
        self._api.request("DELETE", f"/nodes/{self._task_node(upid)}/tasks/{upid}")

    def wait_task(
        self,
        upid: str,
        poll_interval: float = 3.0,
        timeout: float = 6 * 3600,
        sleep: Callable[[float], None] = time.sleep,
        *,
        on_log: Callable[[list[LogLine]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Poll a task until it stops. Returns the final status; raises on non-OK exit.

        Pass ``on_log`` to also tail the task log — each new batch of ``(line_no, text)``
        pairs is handed to it as the task runs. ``should_cancel`` makes the wait
        interruptible (raises ``TaskCancelled``); see :func:`poll_task`.
        """
        log_fn = (lambda start: self.task_log(upid, start)) if on_log else None
        return poll_task(
            self.task_status, upid, poll_interval, timeout, sleep,
            log_fn=log_fn, on_lines=on_log, should_cancel=should_cancel,
        )

    def close(self) -> None:
        self._api.close()

    def __enter__(self) -> PveClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
