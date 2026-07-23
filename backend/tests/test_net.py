"""TCP reachability helpers — socket mocked — plus interface/WoL-target resolution."""

from __future__ import annotations

from unittest import mock

from app.connectors import net
from app.connectors.net import NetInterface, tcp_reachable, wait_until_reachable


def test_tcp_reachable_true():
    with mock.patch("socket.create_connection") as cc:
        cc.return_value.__enter__.return_value = mock.MagicMock()
        assert tcp_reachable("10.0.0.12", 8007) is True


def test_tcp_reachable_false_on_oserror():
    with mock.patch("socket.create_connection", side_effect=OSError("refused")):
        assert tcp_reachable("10.0.0.12", 8007) is False


def test_wait_until_reachable_succeeds_after_retries():
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("not yet")
        return mock.MagicMock()

    with mock.patch("socket.create_connection", side_effect=flaky):
        ok = wait_until_reachable(
            "10.0.0.12", 8007, timeout=10, interval=0, sleep=lambda _s: None
        )
    assert ok is True
    assert calls["n"] == 3


def test_wait_until_reachable_gives_up_immediately_when_cancelled():
    # A cancelled run must not sit through the full wake timeout (11.2). Reported as False,
    # same as a timeout, because "the PBS isn't up" is the state the caller has to handle.
    with mock.patch("socket.create_connection", side_effect=OSError("down")) as sock:
        ok = wait_until_reachable(
            "10.0.0.12",
            8007,
            timeout=600,
            interval=0,
            sleep=lambda _s: None,
            should_cancel=lambda: True,
        )
    assert ok is False
    assert sock.call_count == 0  # bailed before even trying to connect


def test_wait_until_reachable_times_out():
    with mock.patch("socket.create_connection", side_effect=OSError("down")):
        ok = wait_until_reachable(
            "10.0.0.12", 8007, timeout=0, interval=0, sleep=lambda _s: None
        )
    assert ok is False


# --- interfaces + WoL target -------------------------------------------------

_LAN = NetInterface(name="eth0", address="192.0.2.21", netmask="255.255.255.0")
_OTHER = NetInterface(name="vmnet", address="10.10.0.5", netmask="255.255.255.0")


def test_net_interface_broadcast_and_contains():
    assert _LAN.broadcast == "192.0.2.255"
    assert _LAN.contains("192.0.2.213") is True
    assert _LAN.contains("10.0.0.9") is False


def test_wol_target_uses_subnet_broadcast_of_matching_interface(monkeypatch):
    monkeypatch.setattr(net, "list_interfaces", lambda: [_OTHER, _LAN])
    # No interface named -> auto-pick the NIC whose subnet holds the PBS.
    dest, source_ip = net.wol_target("192.0.2.213")
    assert dest == "192.0.2.255"
    assert source_ip == "192.0.2.21"


def test_wol_target_honours_explicit_interface(monkeypatch):
    monkeypatch.setattr(net, "list_interfaces", lambda: [_LAN, _OTHER])
    dest, source_ip = net.wol_target("192.0.2.213", "eth0")
    assert dest == "192.0.2.255"
    assert source_ip == "192.0.2.21"


def test_wol_target_falls_back_to_host_when_no_subnet_match(monkeypatch):
    monkeypatch.setattr(net, "list_interfaces", lambda: [_OTHER])
    # PBS not on any local subnet -> unicast to the host itself.
    dest, source_ip = net.wol_target("192.0.2.213")
    assert dest == "192.0.2.213"
    assert source_ip is None
