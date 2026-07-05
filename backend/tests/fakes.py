"""In-memory connector fakes for the backup-cycle / service tests (no real PVE/PBS)."""

from __future__ import annotations

from collections.abc import Callable

from app.connectors.errors import ConnectorError, TaskError
from app.connectors.pbs import DatastoreStatus, NodeLoad
from app.connectors.pve import Guest
from app.jobs.deps import CycleDeps


class UnreachablePve:
    """A PVE client whose every call fails — for connector-error paths."""

    def __enter__(self) -> UnreachablePve:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def list_guests(self):
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
        self.vzdump_args: dict | None = None

    def __enter__(self) -> FakePve:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def list_guests(self) -> list[Guest]:
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
    ) -> str:
        self.vzdump_args = {
            "storage": storage,
            "vmids": vmids,
            "all_guests": all_guests,
            "mode": mode,
            "prune_backups": prune_backups,
            "bwlimit": bwlimit,
        }
        return "UPID:pve:backup"

    def wait_task(self, upid: str, poll_interval=None, on_log=None, **_) -> dict:
        if on_log and self.log_lines:
            on_log(list(enumerate(self.log_lines, start=1)))
        if self.fail_task:
            raise TaskError("vzdump failed", exit_status="job errors")
        return {"status": "stopped", "exitstatus": "OK"}


class FakePbs:
    def __init__(
        self,
        fail_task: bool = False,
        total: int = 8_000_000_000,
        used: int = 2_000_000_000,
        avail: int = 6_000_000_000,
        snapshots: dict[int, int] | None = None,
        log_lines: list[str] | None = None,
    ):
        self.fail_task = fail_task
        self.gc_started = False
        self.verify_started = False
        self.verify_args: dict | None = None
        self.log_lines = log_lines or []
        self._total = total
        self._used = used
        self._avail = avail
        self._snapshots = snapshots or {}

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

    def wait_task(self, upid: str, poll_interval=None, on_log=None, **_) -> dict:
        if on_log and self.log_lines:
            on_log(list(enumerate(self.log_lines, start=1)))
        if self.fail_task:
            raise TaskError("gc failed", exit_status="error")
        return {"status": "stopped", "exitstatus": "OK"}

    def datastore_status(self) -> DatastoreStatus:
        return DatastoreStatus(total=self._total, used=self._used, avail=self._avail)

    def node_status(self) -> NodeLoad:
        return NodeLoad(cpu=7, mem=38, uptime=3600)

    def latest_backups(self) -> dict[int, int]:
        return dict(self._snapshots)


class FakePower:
    def __init__(self, *, fail: bool = False):
        self.powered_off = False
        self.fail = fail

    def poweroff(self) -> None:
        if self.fail:
            raise RuntimeError("poweroff failed")
        self.powered_off = True


def make_deps(
    *,
    pve: FakePve | None = None,
    pbs: FakePbs | None = None,
    power: FakePower | None = None,
    reachable: bool | Callable[[], bool] = True,
    pbs_idle: bool | Callable[[], bool] = True,
    wol=None,
    notify=None,
) -> tuple[CycleDeps, FakePve, FakePbs, FakePower]:
    pve = pve or FakePve()
    pbs = pbs or FakePbs()
    power = power or FakePower()
    # ``reachable`` / ``pbs_idle`` may each be a constant or a zero-arg callable, so a test
    # can simulate the box coming up only after a retry (e.g. iter([False, True])) or an
    # exception from the idle check.
    wait = reachable if callable(reachable) else (lambda: reachable)
    idle = pbs_idle if callable(pbs_idle) else (lambda: pbs_idle)
    deps = CycleDeps(
        build_pve=lambda _c: pve,
        build_pbs=lambda _c: pbs,
        build_power=lambda _c: power,
        send_wol=wol or (lambda _c: None),
        wait_reachable=lambda _c: wait(),
        wait_pbs_idle=lambda _c: idle(),
        notify=notify or (lambda _c, _r, _d=None: None),
    )
    return deps, pve, pbs, power
