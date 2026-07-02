"""Wake-on-LAN packet building and sending."""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from app.connectors.errors import WolError
from app.connectors.wol import build_magic_packet, normalize_mac, send_magic_packet

MAC = "00:11:22:33:44:55"
MAC_BYTES = bytes.fromhex("001122334455")


def test_normalize_mac_accepts_separators():
    assert normalize_mac("00:11:22:33:44:55") == MAC_BYTES
    assert normalize_mac("00-11-22-33-44-55") == MAC_BYTES
    assert normalize_mac("001122334455") == MAC_BYTES
    assert normalize_mac("  00:11:22:33:44:55 ") == MAC_BYTES


@pytest.mark.parametrize("bad", ["", "zz:zz:zz:zz:zz:zz", "00:11:22:33:44", "00:11:22:33:44:55:99"])
def test_normalize_mac_rejects_bad(bad):
    with pytest.raises(WolError):
        normalize_mac(bad)


def test_build_magic_packet_structure():
    packet = build_magic_packet(MAC)
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == MAC_BYTES
    # MAC repeated 16 times after the 6-byte header.
    assert packet[6:] == MAC_BYTES * 16


def test_send_magic_packet_broadcasts():
    fake = mock.MagicMock()
    with mock.patch("socket.socket") as sock_ctor:
        sock_ctor.return_value.__enter__.return_value = fake
        send_magic_packet(MAC, broadcast="192.168.1.255", port=9, source_ip="192.168.1.10")

    fake.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    fake.bind.assert_called_once_with(("192.168.1.10", 0))
    sent_packet, addr = fake.sendto.call_args.args
    assert sent_packet == build_magic_packet(MAC)
    assert addr == ("192.168.1.255", 9)


def test_send_magic_packet_wraps_oserror():
    fake = mock.MagicMock()
    fake.sendto.side_effect = OSError("network unreachable")
    with mock.patch("socket.socket") as sock_ctor:
        sock_ctor.return_value.__enter__.return_value = fake
        with pytest.raises(WolError):
            send_magic_packet(MAC)
