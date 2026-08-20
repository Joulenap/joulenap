"""The four power operations behind ``LeaseDeps.default()``.

``test_lease.py`` drives the refcounting and the wake/power-off decisions through
``FakeBox``, which is the right way to test that logic and also means the production
implementations never ran. These are the functions that turn a ``PbsDevice`` into an actual
magic packet, an actual TLS-pinned PBS client and an actual SSH poweroff, so a mistake here
is invisible to every other test and only shows up as a backup server that never wakes or
never sleeps.

Same shape as ``test_deps.py``: patch the module's own globals, assert the wiring.
"""

from __future__ import annotations

import ssl

import pytest

from app.config import PbsDevice
from app.jobs import lease


def make_pbs(**over) -> PbsDevice:
    base = {
        "id": "pbs-01",
        "host": "192.0.2.20",
        "datastore": "backup",
        "mac": "00:11:22:33:44:55",
        "api_token_id": "root@pam!joulenap",
        "api_token_secret": "secret",
        "ssh_key_path": "/data/id_ed25519",
    }
    return PbsDevice(**{**base, **over})


def test_default_wires_the_production_implementations():
    # The dataclass is built by hand, so a field could silently point at the wrong callable.
    deps = lease.LeaseDeps.default()
    assert (deps.send_wol, deps.wait_reachable, deps.wait_idle, deps.poweroff) == (
        lease._send_wol,
        lease._wait_reachable,
        lease._wait_idle,
        lease._poweroff,
    )


# --- wake ---------------------------------------------------------------------


def test_send_wol_targets_the_subnet_broadcast_not_the_whole_network(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        lease.net, "wol_target", lambda host, iface: captured.update(host=host, iface=iface)
        or ("192.0.2.255", "192.0.2.5")
    )
    sent = {}
    monkeypatch.setattr(
        lease,
        "send_magic_packet",
        lambda mac, broadcast, source_ip: sent.update(
            mac=mac, broadcast=broadcast, source_ip=source_ip
        ),
    )

    lease._send_wol(make_pbs(wol_broadcast_iface="eth0"))

    assert captured == {"host": "192.0.2.20", "iface": "eth0"}
    # The packet is scoped to the PBS's own segment and bound to that NIC's address.
    assert sent == {
        "mac": "00:11:22:33:44:55",
        "broadcast": "192.0.2.255",
        "source_ip": "192.0.2.5",
    }


def test_wait_reachable_probes_the_devices_own_host_and_port(monkeypatch):
    captured = {}

    def fake_wait(host, port, timeout, should_cancel=None):
        captured.update(host=host, port=port, timeout=timeout, cancel=should_cancel)
        return True

    monkeypatch.setattr(lease.net, "wait_until_reachable", fake_wait)

    def probe() -> bool:
        return False

    assert lease._wait_reachable(make_pbs(port=8123), 45, probe) is True

    assert captured == {"host": "192.0.2.20", "port": 8123, "timeout": 45, "cancel": probe}


def test_wait_reachable_passes_a_zero_timeout_through(monkeypatch):
    # timeout=0 is the "is it already awake?" single probe the lease takes before any wake;
    # rewriting it to a default would make every acquire sit through a wake wait.
    seen = {}
    monkeypatch.setattr(
        lease.net,
        "wait_until_reachable",
        lambda host, port, timeout, should_cancel=None: seen.update(timeout=timeout) or False,
    )

    assert lease._wait_reachable(make_pbs(), 0) is False
    assert seen["timeout"] == 0


# --- idle check ---------------------------------------------------------------


class _FakePbsClient:
    """Records how it was built and what timeout the idle check asked for."""

    last: dict = {}

    def __init__(self, **kwargs):
        _FakePbsClient.last = dict(kwargs)
        self.waited = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def wait_until_idle(self, timeout):
        _FakePbsClient.last["idle_timeout"] = timeout
        return True


def test_wait_idle_pins_tls_when_the_device_has_a_fingerprint(monkeypatch):
    sentinel = ssl.create_default_context()
    pinned = {}
    monkeypatch.setattr(
        lease.tls,
        "pinned_ssl_context",
        lambda host, port, fp: pinned.update(host=host, port=port, fp=fp) or sentinel,
    )
    monkeypatch.setattr(lease, "PbsClient", _FakePbsClient)

    assert lease._wait_idle(make_pbs(fingerprint="AA:BB:CC")) is True

    assert pinned == {"host": "192.0.2.20", "port": 8007, "fp": "AA:BB:CC"}
    assert _FakePbsClient.last["verify"] is sentinel
    # The device's own budget, not a library default: 0 means "don't wait at all".
    assert _FakePbsClient.last["idle_timeout"] == 600


def test_wait_idle_does_not_pin_without_a_fingerprint(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not pin without a fingerprint")

    monkeypatch.setattr(lease.tls, "pinned_ssl_context", boom)
    monkeypatch.setattr(lease, "PbsClient", _FakePbsClient)

    lease._wait_idle(make_pbs(poweroff_task_wait=90))

    assert _FakePbsClient.last["verify"] is False
    assert _FakePbsClient.last["idle_timeout"] == 90


def test_wait_idle_passes_the_devices_credentials_and_datastore(monkeypatch):
    monkeypatch.setattr(lease, "PbsClient", _FakePbsClient)

    lease._wait_idle(make_pbs(datastore="offsite"))

    built = _FakePbsClient.last
    assert built["host"] == "192.0.2.20"
    assert built["datastore"] == "offsite"
    assert built["token_id"] == "root@pam!joulenap"
    assert built["token_secret"] == "secret"


# --- power off ----------------------------------------------------------------


def test_poweroff_uses_the_devices_ssh_user_and_key(monkeypatch):
    built = {}
    calls = []

    class FakePower:
        def __init__(self, **kwargs):
            built.update(kwargs)

        def poweroff(self):
            calls.append("poweroff")

    monkeypatch.setattr(lease, "PbsPower", FakePower)

    lease._poweroff(make_pbs(ssh_user="backup", ssh_key_path="/data/key"))

    assert built == {"host": "192.0.2.20", "user": "backup", "key_path": "/data/key"}
    assert calls == ["poweroff"]


def test_poweroff_lets_the_error_out_for_the_lease_to_swallow(monkeypatch):
    # PowerLease._power_off is what decides a failed shutdown is non-fatal; this layer must
    # not swallow it first, or the lease could never report LEFT_ON.
    class FakePower:
        def __init__(self, **_kwargs):
            pass

        def poweroff(self):
            raise RuntimeError("ssh refused")

    monkeypatch.setattr(lease, "PbsPower", FakePower)

    with pytest.raises(RuntimeError, match="ssh refused"):
        lease._poweroff(make_pbs())
