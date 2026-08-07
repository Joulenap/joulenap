"""Per-PBS power lease: who woke a backup server, and who may put it back to sleep.

With per-route schedules several routes can target the same PBS minutes apart, so no single
cycle can decide when the box goes back to sleep. The lease centralises that decision: the
first holder wakes it (or finds it already awake), every holder releases when its route is
done, and the *last* release powers it off — unless a queued route still needs the box, the
run failed, or the user asked to keep it on.

It is also the single place that knows about an unmanaged PBS (``managed_power: false``):
there, acquiring is a reachability check and releasing does nothing, so cycles stay
oblivious to the difference.
"""

from __future__ import annotations

import logging
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ..config import PbsDevice
from ..connectors import net, tls
from ..connectors.pbs import PbsClient
from ..connectors.power import PbsPower
from ..connectors.wol import send_magic_packet
from ..notify.messages import LocalizedError

log = logging.getLogger("joulenap.lease")


class PbsUnreachableError(LocalizedError):
    """The PBS never answered: it could not be woken, or it is an unmanaged box that is off."""


class ReleaseOutcome(StrEnum):
    """What became of a box when a run dropped its lease.

    Only :attr:`LEFT_ON` means "still burning power, and nothing is going to fix that" —
    the others are boxes Joulenap either put to sleep, never powers, or will power off at
    the end of the run that still holds them. The value doubles as the POWEROFF step's
    English detail in the run timeline; :attr:`key` is the same fact as a translatable
    code, which is what the step actually stores alongside it.
    """

    POWERED_OFF = "powered off"
    STILL_NEEDED = "left on: still needed by another run"
    UNMANAGED = "left on: Joulenap does not manage this box's power"
    LEFT_ON = "left powered on"

    @property
    def key(self) -> str:
        """This outcome's key in ``notify.messages._DETAILS`` — the member name, lowercased.

        Derived rather than mapped so a new member cannot be added without a key: the
        i18n parity test walks the enum and fails on any member the packs don't cover.
        """
        return self.name.lower()


# --- device-shaped connector calls -------------------------------------------
# jobs/deps.py has the same four operations bound to the 0.9 single-PBS ``Config``. These
# take the device a route points at; the Config-shaped originals die with the old cycle.


def _send_wol(pbs: PbsDevice) -> None:
    # Scope the packet to the PBS's subnet-directed broadcast (see net.wol_target) rather
    # than blasting the whole network.
    dest, source_ip = net.wol_target(pbs.host, pbs.wol_broadcast_iface)
    send_magic_packet(pbs.mac, broadcast=dest, source_ip=source_ip)


def _wait_reachable(
    pbs: PbsDevice, timeout: float, should_cancel: Callable[[], bool] | None = None
) -> bool:
    # timeout=0 is a single probe: one connect attempt, no sleep.
    return net.wait_until_reachable(
        pbs.host, pbs.port, timeout=timeout, should_cancel=should_cancel
    )


def _wait_idle(pbs: PbsDevice) -> bool:
    verify: bool | ssl.SSLContext = False
    if pbs.fingerprint:
        verify = tls.pinned_ssl_context(pbs.host, pbs.port, pbs.fingerprint)
    with PbsClient(
        host=pbs.host,
        datastore=pbs.datastore,
        token_id=pbs.api_token_id,
        token_secret=pbs.api_token_secret,
        port=pbs.port,
        verify=verify,
    ) as client:
        return client.wait_until_idle(timeout=pbs.poweroff_task_wait)


def _poweroff(pbs: PbsDevice) -> None:
    PbsPower(host=pbs.host, user=pbs.ssh_user, key_path=pbs.ssh_key_path).poweroff()


@dataclass
class LeaseDeps:
    """The four power operations, bundled so tests can inject fakes (like CycleDeps)."""

    send_wol: Callable[[PbsDevice], None]
    # (device, timeout seconds, cancel probe) -> reachable. Untyped args because the fakes
    # take fewer of them; the production implementation is ``_wait_reachable`` above.
    wait_reachable: Callable[..., bool]
    wait_idle: Callable[[PbsDevice], bool]
    poweroff: Callable[[PbsDevice], None]

    @classmethod
    def default(cls) -> LeaseDeps:
        return cls(
            send_wol=_send_wol,
            wait_reachable=_wait_reachable,
            wait_idle=_wait_idle,
            poweroff=_poweroff,
        )


@dataclass
class LeaseState:
    """How many runs currently hold a PBS, and whether it was already up when the first
    one arrived (``was_awake`` means "skip the Wake-on-LAN", nothing more — a box Joulenap
    found awake is still powered off at the end, or a failed power-off would leave it on
    forever)."""

    holders: int = 0
    was_awake: bool = False


def lease_key(pbs: PbsDevice) -> str:
    """The lease's identity for a device: the *machine*, not the config entry.

    Power is physical. Two devices can be two datastores on one box (that is a supported
    setup), and an SSH poweroff takes down every PBS instance on it — so keying the refcount
    per device meant one run could hold two leases on one machine, power it off on the first
    release, and then report the second as "left powered on" after failing to reach a box it
    had itself just shut down.

    The port is deliberately not part of the key, for that same reason. Host is free text on
    both sides, so it is normalised the way ``discovery.match_storages_to_pbss`` normalises
    it; a device with no host yet (legal mid-wizard) falls back to its id so half-configured
    entries don't all collide on ``""``.
    """
    return pbs.host.strip().lower() or pbs.id


class PowerLease:
    """Refcounted wake/power-off, one entry per physical machine (see :func:`lease_key`).

    ponytail: the refcount lives in this process's memory, which is correct while Joulenap
    runs as a single instance (the whole app assumes that). Two instances against one PBS
    would need the lease in SQLite with a TTL — or just don't run two.
    """

    def __init__(
        self,
        deps: LeaseDeps,
        *,
        pending_pbs_keys: Callable[[], set[str]] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ):
        self._deps = deps
        # Which machines the *queued* runs still need, as lease keys. Injected so the lease
        # never has to know about the queue or the config.
        self._pending = pending_pbs_keys or (lambda: set())
        self._cancelled = cancelled or (lambda: False)
        self._lock = threading.Lock()
        self._state: dict[str, LeaseState] = {}

    def state(self, pbs: PbsDevice) -> LeaseState:
        """A snapshot of one machine's lease — introspection for the API/UI.

        Takes the device rather than an id because the key is the machine: every device on a
        held box reports the same holders, which is what disables the ⏻ button on all of
        them.
        """
        with self._lock:
            held = self._state.get(lease_key(pbs))
            return LeaseState(held.holders, held.was_awake) if held else LeaseState()

    # --- manual power buttons ------------------------------------------------
    #
    # The topology card's ⏻ acts on the box directly, outside any run. They live here so the
    # API never has to reach for the connector helpers itself, and so there is exactly one
    # place that knows how to wake and shut down a PBS.

    def wake(self, pbs: PbsDevice) -> None:
        """Send the magic packet and return. The button doesn't wait for the box to come up:
        a wake takes minutes and the topology already polls each device's state."""
        self._deps.send_wol(pbs)

    def power_off_now(self, pbs: PbsDevice) -> None:
        """Shut a box down on the user's explicit request, raising if it fails.

        No idle-wait and no refcount check, unlike :meth:`release`: the caller has confirmed
        no run holds the lease, and a click means *now*. Failures propagate here — a manual
        action that silently does nothing is worse than an error message, whereas a failed
        power-off at the end of a run costs energy, not correctness.
        """
        self._deps.poweroff(pbs)

    def acquire(self, pbs: PbsDevice) -> bool:
        """Make sure ``pbs`` is up and count this run as a holder. Returns ``was_awake``.

        Raises :class:`PbsUnreachableError` if the box never answers; the holder is only
        registered on success, so a failed acquire needs no release.
        """
        key = lease_key(pbs)
        with self._lock:
            held = self._state.get(key)
            if held is not None and held.holders:
                held.holders += 1
                log.info("PBS %s already awake and leased by %d run(s)", pbs.id, held.holders)
                return held.was_awake

        # Nobody holds it, so this run decides whether the box needs waking. Done outside the
        # lock: a wake takes minutes and must not block another PBS's lease or the UI's
        # ``state()`` read.
        was_awake = self._bring_up(pbs)

        with self._lock:
            # setdefault + increment, not a plain assignment: two first-acquires racing on the
            # same box would otherwise each write holders=1 and the first release would power
            # it off under the second.
            held = self._state.setdefault(key, LeaseState(was_awake=was_awake))
            held.holders += 1
        return was_awake

    def release(self, pbs: PbsDevice, *, power_off: bool = True) -> ReleaseOutcome:
        """Drop this run's hold and power the box down if it is now safe to. Returns *why*
        the box ended up in the state it did.

        ``power_off`` is the caller's policy (a failed run leaves the PBS on for inspection,
        a manual run honours the "power off when finished" toggle). The lease adds the
        conditions the caller cannot see: no other holder, and no *queued* route that still
        needs this box.

        Four outcomes rather than a bool because only two of them cost the user power, and
        the lease is the only place that can tell them apart — a caller re-deriving "was
        that a real 'left on'?" would have to duplicate every condition below.
        """
        key = lease_key(pbs)
        with self._lock:
            held = self._state.get(key)
            if held is None or not held.holders:
                log.warning("Release of an unheld lease on PBS %s — ignoring", pbs.id)
                # A bug, not a policy: say "left on" so it is at least visible.
                return ReleaseOutcome.LEFT_ON
            held.holders -= 1
            remaining = held.holders
            if not remaining:
                del self._state[key]

        # Order matters for the *reason*, not for the action — every branch below stops the
        # power-off equally. The caller's policy is checked last on purpose: "this run
        # doesn't want to power off" is only the real explanation once the box is one we
        # could have powered off and nobody else needs it.
        if remaining:
            log.info("PBS %s still held by %d run(s); leaving it on", pbs.id, remaining)
            return ReleaseOutcome.STILL_NEEDED
        if not pbs.managed_power:
            # An always-on / cloud-hosted PBS: Joulenap never touches its power.
            return ReleaseOutcome.UNMANAGED
        if key in self._pending():
            log.info("PBS %s is needed by a queued route; leaving it on", pbs.id)
            return ReleaseOutcome.STILL_NEEDED
        if not power_off:
            return ReleaseOutcome.LEFT_ON
        # False here is a busy box we declined to cut off, or a poweroff that errored — both
        # leave it burning power.
        return ReleaseOutcome.POWERED_OFF if self._power_off(pbs) else ReleaseOutcome.LEFT_ON

    # --- internals -----------------------------------------------------------

    def _bring_up(self, pbs: PbsDevice) -> bool:
        """Get the box answering, waking it if we are allowed to. Returns ``was_awake``.

        Whoever sends the magic packet owns the box until someone releases it, so this method
        holds one rule either side of that packet: don't send one for a run that is already
        stopping, and once one is sent, see the wake through so ``acquire`` can hand the
        caller a lease. Everything that puts a box back to sleep hangs off that lease.
        """
        # No cancel probe on the instant check: there is no wait to abandon, and poisoning it
        # made an already-awake box read as asleep — which then earned it a magic packet.
        if self._deps.wait_reachable(pbs, 0):
            return True
        if not pbs.managed_power:
            raise PbsUnreachableError(
                f"PBS '{pbs.id}' ({pbs.host}:{pbs.port}) is not reachable, and Joulenap does "
                "not manage its power (managed_power: false) so it cannot wake it — turn the "
                "box on, or enable managed power for this device."
            )
        if self._cancelled():
            # Stopped before we woke anything: send nothing, so there is nothing to clean up.
            raise PbsUnreachableError("cancelled_waiting", pbs=pbs.id)

        # Total wake attempts = wol_retries + 1: a dropped packet or a slow boot shouldn't
        # fail the whole run.
        attempts = pbs.wol_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            self._deps.send_wol(pbs)
            if self._deps.wait_reachable(pbs, pbs.wait_timeout, self._cancelled):
                return False
            # A cancelled wait returns False exactly like a timeout, so check *why* before
            # burning the remaining attempts on a run the user already stopped.
            if self._cancelled():
                # Our packet is already on the wire: the box is coming up whatever we do now.
                # Wait it out — without the cancel probe, which would return instantly — so
                # the caller gets a lease to release, because that release is the only thing
                # that will ever put the box back to sleep. Bounded by what is left of *this*
                # attempt, so stopping a run never costs more than the wake it interrupted.
                grace = pbs.wait_timeout - (time.monotonic() - started)
                if grace > 0 and self._deps.wait_reachable(pbs, grace):
                    return False
                # ponytail: a box that answers after the grace stays on until someone
                # notices. Catching that needs the wake remembered across runs — a lease in
                # SQLite — which is a bigger change than the case deserves.
                raise PbsUnreachableError("cancelled_waiting", pbs=pbs.id)
            if attempt < attempts:
                log.warning(
                    "PBS %s still down after wake attempt %d/%d (%ds); re-sending Wake-on-LAN",
                    pbs.id,
                    attempt,
                    attempts,
                    pbs.wait_timeout,
                )
        raise PbsUnreachableError(
            "pbs_unreachable",
            pbs=pbs.id,
            host=pbs.host,
            port=pbs.port,
            attempts=attempts,
            timeout=pbs.wait_timeout,
        )

    def _power_off(self, pbs: PbsDevice) -> bool:
        """Wait for the box to go idle, then shut it down. Never raises: the run's data is
        already safe, so a failed power-off costs energy, not correctness."""
        if pbs.poweroff_task_wait > 0:
            try:
                idle = self._deps.wait_idle(pbs)
            except Exception as exc:  # noqa: BLE001 — a flaky check must not keep it awake
                log.warning("Could not check PBS %s tasks (%s); powering off anyway", pbs.id, exc)
                idle = True
            if not idle:
                log.warning(
                    "PBS %s still running a task after %ds; leaving it on rather than "
                    "interrupting it",
                    pbs.id,
                    pbs.poweroff_task_wait,
                )
                return False
        try:
            self._deps.poweroff(pbs)
        except Exception as exc:  # noqa: BLE001 — best effort, see the docstring
            log.warning("Power-off of PBS %s failed, box left on: %s", pbs.id, exc)
            return False
        log.info("Powered off PBS %s", pbs.id)
        return True
