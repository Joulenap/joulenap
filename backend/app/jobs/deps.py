"""Connector dependencies for the cycle jobs, bundled so tests can inject fakes.

By default each callable builds a real connector from the live config; the backup-cycle
and GC jobs only ever touch the connectors through a :class:`CycleDeps`, so a test can
swap in stubs and exercise the full state machine without a real PVE/PBS.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Config
from ..connectors import net, tls
from ..connectors.pbs import DatastoreStatus, PbsClient
from ..connectors.power import PbsPower
from ..connectors.pve import PveClient
from ..connectors.wol import send_magic_packet
from ..db.models import Run
from ..notify import NotificationService


def _build_pve(config: Config) -> PveClient:
    p = config.pve
    return PveClient(
        host=p.host,
        node=p.node,
        token_id=p.api_token_id,
        token_secret=p.api_token_secret,
        port=p.port,
        verify_tls=p.verify_tls,
    )


def _build_pbs(config: Config) -> PbsClient:
    p = config.pbs
    verify: bool | ssl.SSLContext = False
    if p.fingerprint:
        # Pin the stored fingerprint (captured by the wizard from the PVE storage config).
        verify = tls.pinned_ssl_context(p.host, p.port, p.fingerprint)
    return PbsClient(
        host=p.host,
        datastore=p.datastore,
        token_id=p.api_token_id,
        token_secret=p.api_token_secret,
        port=p.port,
        verify=verify,
    )


def _build_power(config: Config) -> PbsPower:
    p = config.pbs
    return PbsPower(host=p.host, user=p.ssh_user, key_path=p.ssh_key_path)


def _send_wol(config: Config) -> None:
    # Pre-setup (no PBS host configured) we don't know the target subnet, so use the global
    # broadcast. Once the PBS host is known we scope the packet to the PBS's subnet-directed
    # broadcast and bind to the chosen (or auto-detected) interface, so we wake the PBS
    # without blasting the whole network. See net.wol_target.
    p = config.pbs
    if p.host:
        dest, source_ip = net.wol_target(p.host, p.wol_broadcast_iface)
    else:
        dest, source_ip = "255.255.255.255", None
    send_magic_packet(p.mac, broadcast=dest, source_ip=source_ip)


def _wait_reachable(config: Config, should_cancel: Callable[[], bool] | None = None) -> bool:
    p = config.pbs
    return net.wait_until_reachable(
        p.host, p.port, timeout=p.wait_timeout, should_cancel=should_cancel
    )


def _wait_pbs_idle(config: Config) -> bool:
    with _build_pbs(config) as pbs:
        return pbs.wait_until_idle(timeout=config.pbs.poweroff_task_wait)


def _notify(config: Config, run: Run, datastore: DatastoreStatus | None = None) -> None:
    NotificationService().send_run_result(config, run, datastore)


@dataclass
class CycleDeps:
    """The connector entry points the jobs use, each taking the live :class:`Config`."""

    build_pve: Callable[[Config], PveClient]
    build_pbs: Callable[[Config], PbsClient]
    build_power: Callable[[Config], PbsPower]
    send_wol: Callable[[Config], None]
    wait_reachable: Callable[..., bool]
    wait_pbs_idle: Callable[[Config], bool]
    notify: Callable[[Config, Run, DatastoreStatus | None], None]
    # True once the user has asked to stop the in-flight run. Wired by JobService to its own
    # cancel event and read live, so the cycle can check it without knowing about the service.
    # Default: nothing ever cancels (tests and direct callers that don't care).
    cancelled: Callable[[], bool] = lambda: False
    # Whether that cancel asked for the PBS to be powered off afterwards (the toggle in the
    # stop dialog). Only meaningful once ``cancelled()`` is True.
    cancel_power_off: Callable[[], bool] = lambda: False

    @classmethod
    def default(cls) -> CycleDeps:
        return cls(
            build_pve=_build_pve,
            build_pbs=_build_pbs,
            build_power=_build_power,
            send_wol=_send_wol,
            wait_reachable=_wait_reachable,
            wait_pbs_idle=_wait_pbs_idle,
            notify=_notify,
        )
