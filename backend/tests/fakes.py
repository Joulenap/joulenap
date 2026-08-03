"""In-memory connector fakes for the backup-cycle / service tests (no real PVE/PBS)."""

from __future__ import annotations

from app.connectors.errors import ConnectorError, TaskCancelled, TaskError
from app.connectors.pbs import DatastoreStatus, NodeLoad
from app.connectors.pve import Guest
from app.jobs.deps import CycleDeps
from app.jobs.lease import LeaseDeps


class UnreachablePve:
    """A PVE client whose every call fails — for connector-error paths."""

    def __enter__(self) -> UnreachablePve:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def list_guests(self):
        raise ConnectorError("connection refused")

    def list_cluster_guests(self):
        raise ConnectorError("connection refused")


class FakePve:
    def __init__(
        self,
        guests: list[Guest] | None = None,
        fail_task: bool = False,
        log_lines: list[str] | None = None,
    ):
        self.guests = guests or []
        self.fail_task = fail_task
        self.log_lines = log_lines or []
        self.vzdump_args: dict | None = None  # the last call (0.9 single-vzdump cycle)
        self.vzdump_calls: list[dict] = []  # every call, in order (a route backs up per node)
        self.stopped: list[str] = []  # upids passed to stop_task

    def __enter__(self) -> FakePve:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def list_guests(self) -> list[Guest]:
        return self.guests

    def list_cluster_guests(self) -> list[Guest]:
        return self.guests

    def vzdump(
        self,
        storage,
        *,
        vmids=None,
        all_guests=False,
        mode="snapshot",
        prune_backups=None,
        bwlimit=0,
        node="",
    ) -> str:
        self.vzdump_args = {
            "storage": storage,
            "vmids": vmids,
            "all_guests": all_guests,
            "mode": mode,
            "prune_backups": prune_backups,
            "bwlimit": bwlimit,
            "node": node,
        }
        self.vzdump_calls.append(self.vzdump_args)
        # A real UPID names the node the task runs on, which is how the client routes its
        # task calls — and how a per-node test tells the tasks apart.
        return f"UPID:{node or 'pve'}:backup"

    def wait_task(
        self, upid: str, poll_interval=None, on_log=None, should_cancel=None, **_
    ) -> dict:
        if on_log and self.log_lines:
            on_log(list(enumerate(self.log_lines, start=1)))
        # Mirror poll_task: the cancel probe is checked before reporting a result, so a
        # cycle test can cancel mid-task exactly like the real client would.
        if should_cancel is not None and should_cancel():
            raise TaskCancelled(f"Wait for task {upid} cancelled")
        if self.fail_task:
            raise TaskError("vzdump failed", exit_status="job errors")
        return {"status": "stopped", "exitstatus": "OK"}

    def stop_task(self, upid: str) -> None:
        self.stopped.append(upid)


class FakePbs:
    def __init__(
        self,
        fail_task: bool = False,
        total: int = 8_000_000_000,
        used: int = 2_000_000_000,
        avail: int = 6_000_000_000,
        snapshots: dict[int, int] | None = None,
        log_lines: list[str] | None = None,
        fail_datastore: bool = False,
        gc_log_lines: list[str] | None = None,
        verify_log_lines: list[str] | None = None,
        active_tasks_seq: list[list[dict]] | None = None,
    ):
        self.fail_task = fail_task
        self.gc_started = False
        self.verify_started = False
        self.stopped: list[str] = []  # upids passed to stop_task
        self.verify_args: dict | None = None
        # Sync route bookkeeping: what a route asked this box to set up and run.
        self.remotes: dict[str, dict] = {}
        self.sync_jobs: dict[str, dict] = {}
        self.sync_runs: list[dict] = []
        self.log_lines = log_lines or []
        self.fail_datastore = fail_datastore
        self.gc_log_lines = gc_log_lines
        self.verify_log_lines = verify_log_lines
        self._total = total
        self._used = used
        self._avail = avail
        self._snapshots = snapshots or {}
        # Each active_tasks() call pops the next entry; empty (idle) once exhausted.
        self.active_tasks_seq = active_tasks_seq or []

    def __enter__(self) -> FakePbs:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def start_gc(self) -> str:
        self.gc_started = True
        return "UPID:pbs:gc"

    def start_verify(
        self, *, ignore_verified: bool = True, outdated_after: int | None = None
    ) -> str:
        self.verify_started = True
        self.verify_args = {"ignore_verified": ignore_verified, "outdated_after": outdated_after}
        return "UPID:pbs:verify"

    def ensure_remote(self, name: str, **kwargs) -> None:
        self.remotes[name] = kwargs

    def ensure_sync_job(self, job_id: str, **kwargs) -> None:
        self.sync_jobs[job_id] = kwargs

    def run_sync_job(self, job_id: str) -> str:
        self.sync_runs.append({"id": job_id})
        return "UPID:pbs:sync"

    def wait_task(
        self, upid: str, poll_interval=None, on_log=None, should_cancel=None, **_
    ) -> dict:
        lines = self.log_lines
        if upid.endswith(":gc") and self.gc_log_lines is not None:
            lines = self.gc_log_lines
        elif upid.endswith(":verify") and self.verify_log_lines is not None:
            lines = self.verify_log_lines
        if on_log and lines:
            on_log(list(enumerate(lines, start=1)))
        if should_cancel is not None and should_cancel():
            raise TaskCancelled(f"Wait for task {upid} cancelled")
        if self.fail_task:
            raise TaskError("gc failed", exit_status="error")
        return {"status": "stopped", "exitstatus": "OK"}

    def stop_task(self, upid: str) -> None:
        self.stopped.append(upid)

    def datastore_status(self) -> DatastoreStatus:
        if self.fail_datastore:
            raise ConnectorError("datastore read failed")
        return DatastoreStatus(total=self._total, used=self._used, avail=self._avail)

    def node_status(self) -> NodeLoad:
        return NodeLoad(cpu=7, mem=38, uptime=3600)

    def latest_backups(self) -> dict[int, int]:
        return dict(self._snapshots)

    def active_tasks(self) -> list[dict]:
        return self.active_tasks_seq.pop(0) if self.active_tasks_seq else []


class FakePower:
    def __init__(self, *, fail: bool = False):
        self.powered_off = False
        self.fail = fail

    def poweroff(self) -> None:
        if self.fail:
            raise RuntimeError("poweroff failed")
        self.powered_off = True


class FakeBox:
    """A fake PBS box for the power lease: does it answer, and what did the lease do to it.

    ``reachable`` is either a constant or a list of answers consumed one probe at a time
    (the last one repeats), so a test can say "down, then up after the first wake".
    """

    def __init__(
        self,
        reachable: bool | list[bool] = True,
        idle: bool = True,
        idle_error: Exception | None = None,
        poweroff_error: Exception | None = None,
    ):
        self._reachable = reachable
        self.idle = idle
        self.idle_error = idle_error
        self.poweroff_error = poweroff_error
        self.wol: list[str] = []  # pbs ids a magic packet was sent to, in order
        self.waits: list[float] = []  # the timeout of every reachability probe
        self.poweroffs: list[str] = []  # pbs ids actually powered off

    def _answer(self) -> bool:
        if isinstance(self._reachable, list):
            return self._reachable.pop(0) if len(self._reachable) > 1 else self._reachable[0]
        return self._reachable

    def deps(self) -> LeaseDeps:
        def wait_reachable(pbs, timeout, _should_cancel=None) -> bool:
            self.waits.append(timeout)
            return self._answer()

        def send_wol(pbs) -> None:
            self.wol.append(pbs.id)

        def wait_idle(_pbs) -> bool:
            if self.idle_error is not None:
                raise self.idle_error
            return self.idle

        def poweroff(pbs) -> None:
            if self.poweroff_error is not None:
                raise self.poweroff_error
            self.poweroffs.append(pbs.id)

        return LeaseDeps(
            send_wol=send_wol,
            wait_reachable=wait_reachable,
            wait_idle=wait_idle,
            poweroff=poweroff,
        )


def make_deps(
    *,
    pve: FakePve | None = None,
    pbs: FakePbs | None = None,
    notify=None,
    pves: dict[str, object] | None = None,
    pbss: dict[str, object] | None = None,
) -> tuple[CycleDeps, FakePve, FakePbs]:
    """Build a :class:`CycleDeps` wired to in-memory fakes.

    ``pves``/``pbss`` map a *device id* to its fake, so a route test with several sources
    gives one entry per device; ``pve``/``pbs`` are the fallback every unlisted device
    resolves to, which is all a single-source route needs.
    """
    pve = pve or FakePve()
    pbs = pbs or FakePbs()
    deps = CycleDeps(
        connect_pve=lambda device: (pves or {}).get(device.id, pve),
        connect_pbs=lambda device: (pbss or {}).get(device.id, pbs),
        notify=notify or (lambda _ctx: None),
    )
    return deps, pve, pbs
