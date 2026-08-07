"""Connector dependencies for the cycle jobs, bundled so tests can inject fakes.

By default each callable builds a real connector from the device a route points at; the
route cycles only ever touch the connectors through a :class:`CycleDeps`, so a test can
swap in stubs and exercise the full state machine without a real PVE/PBS.

Power lives elsewhere on purpose: waking and shutting a PBS down is the
:class:`~.lease.PowerLease`'s job (it is the only thing that knows how many runs still
need the box), so there is no ``send_wol``/``poweroff`` here to call by mistake.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..config import PbsDevice, PveDevice
from ..connectors import tls
from ..connectors.pbs import PbsClient
from ..connectors.pve import PveClient
from ..notify import NotificationService
from ..notify.messages import RunContext


def _connect_pve(pve: PveDevice) -> PveClient:
    # No node: a route lists guests cluster-wide and names the node per vzdump call.
    return PveClient(
        host=pve.host,
        token_id=pve.api_token_id,
        token_secret=pve.api_token_secret,
        port=pve.port,
        verify_tls=pve.verify_tls,
    )


def _connect_pbs(pbs: PbsDevice) -> PbsClient:
    verify: bool | ssl.SSLContext = False
    if pbs.fingerprint:
        # Pin the stored fingerprint (captured by the wizard from the PVE storage config).
        verify = tls.pinned_ssl_context(pbs.host, pbs.port, pbs.fingerprint)
    return PbsClient(
        host=pbs.host,
        datastore=pbs.datastore,
        token_id=pbs.api_token_id,
        token_secret=pbs.api_token_secret,
        port=pbs.port,
        verify=verify,
    )


def _notify(ctx: RunContext) -> None:
    NotificationService().send_run_result(ctx)


@dataclass
class CycleDeps:
    """The connector entry points the jobs use, each taking the device it talks to."""

    connect_pve: Callable[[PveDevice], PveClient] = _connect_pve
    connect_pbs: Callable[[PbsDevice], PbsClient] = _connect_pbs
    # Deliver a finished run's notification. Takes the whole RunContext, so a new field on
    # the message never ripples through this seam again.
    notify: Callable[[RunContext], None] = _notify
    # True once the user has asked to stop the in-flight run. Wired by JobService to its own
    # cancel event and read live, so the cycle can check it without knowing about the service.
    # Default: nothing ever cancels (tests and direct callers that don't care).
    cancelled: Callable[[], bool] = lambda: False
    # Whether that cancel asked for the PBS to be powered off afterwards (the toggle in the
    # stop dialog). Only meaningful once ``cancelled()`` is True.
    cancel_power_off: Callable[[], bool] = lambda: False
    # When a given route next fires, for the notification's "next scheduled run" line. Late-
    # bound by main.py: the Scheduler lives on app.state and isn't reachable from inside a
    # running cycle. Default "unknown" — tests and direct callers just omit the line.
    next_run: Callable[[str], datetime | None] = lambda _route_id: None

    @classmethod
    def default(cls) -> CycleDeps:
        return cls()
