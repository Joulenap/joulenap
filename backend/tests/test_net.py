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


# --- the real NIC enumeration -------------------------------------------------
#
# wol_target's tests monkeypatch `net.list_interfaces`, so the psutil walk behind it had
# never executed. It decides which NIC the magic packet is bound to, and getting it wrong
# means a packet that never reaches the PBS.


class _Stat:
    def __init__(self, isup: bool):
        self.isup = isup


class _Addr:
    def __init__(self, family, address, netmask):
        self.family, self.address, self.netmask = family, address, netmask


def _fake_psutil(stats: dict, addrs: dict):
    class FakePsutil:
        @staticmethod
        def net_if_stats():
            return stats

        @staticmethod
        def net_if_addrs():
            return addrs

    return FakePsutil


def test_list_interfaces_keeps_only_up_non_loopback_ipv4(monkeypatch):
    import socket as socket_mod

    monkeypatch.setattr(
        net,
        "psutil",
        _fake_psutil(
            stats={"eth0": _Stat(True), "eth1": _Stat(False), "lo": _Stat(True)},
            addrs={
                "eth0": [
                    _Addr(socket_mod.AF_INET6, "fe80::1", None),  # not IPv4: WoL is IPv4
                    _Addr(socket_mod.AF_INET, "192.168.1.10", "255.255.255.0"),
                ],
                "eth1": [_Addr(socket_mod.AF_INET, "10.0.0.5", "255.0.0.0")],  # down
                "lo": [_Addr(socket_mod.AF_INET, "127.0.0.1", "255.0.0.0")],  # loopback
            },
        ),
    )

    ifaces = net.list_interfaces()

    assert [(i.name, i.address, i.netmask) for i in ifaces] == [
        ("eth0", "192.168.1.10", "255.255.255.0")
    ]
    assert ifaces[0].broadcast == "192.168.1.255"


def test_list_interfaces_defaults_a_missing_netmask_to_a_single_host(monkeypatch):
    # psutil can report an IPv4 address with no netmask; /32 is the safe reading, because
    # guessing a wider subnet would broadcast the packet at machines that are not the PBS.
    import socket as socket_mod

    monkeypatch.setattr(
        net,
        "psutil",
        _fake_psutil(
            stats={"eth0": _Stat(True)},
            addrs={"eth0": [_Addr(socket_mod.AF_INET, "192.168.1.10", None)]},
        ),
    )

    assert net.list_interfaces()[0].netmask == "255.255.255.255"


def test_list_interfaces_keeps_a_nic_psutil_reports_no_stats_for(monkeypatch):
    # `name in stats` guards the isup lookup: an interface missing from net_if_stats is
    # kept rather than dropped, so an unusual NIC never silently disappears.
    import socket as socket_mod

    monkeypatch.setattr(
        net,
        "psutil",
        _fake_psutil(
            stats={},
            addrs={"weird0": [_Addr(socket_mod.AF_INET, "192.168.5.10", "255.255.255.0")]},
        ),
    )

    assert [i.name for i in net.list_interfaces()] == ["weird0"]


def test_find_interface_reads_the_real_enumeration(monkeypatch):
    import socket as socket_mod

    monkeypatch.setattr(
        net,
        "psutil",
        _fake_psutil(
            stats={"eth0": _Stat(True)},
            addrs={"eth0": [_Addr(socket_mod.AF_INET, "192.168.1.10", "255.255.255.0")]},
        ),
    )

    assert net.find_interface("eth0").address == "192.168.1.10"
    assert net.find_interface("eth9") is None
