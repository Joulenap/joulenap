"""Wake-on-LAN: build and broadcast the magic packet that wakes the PBS.

The magic packet is 6 bytes of 0xFF followed by the target MAC repeated 16 times,
sent as a UDP datagram to the broadcast address (port 9 by convention). Joulenap
runs on the LAN (host/macvlan networking) so the broadcast reaches the sleeping PBS.
"""

from __future__ import annotations

import re
import socket

from .errors import WolError

_DEFAULT_BROADCAST = "255.255.255.255"
_DEFAULT_PORT = 9
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?)(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}$")


def normalize_mac(mac: str) -> bytes:
    """Parse a MAC like ``00:11:22:33:44:55`` (or ``-``/no separators) into 6 bytes."""
    cleaned = mac.strip()
    if not _MAC_RE.match(cleaned):
        raise WolError(f"Invalid MAC address: {mac!r}")
    hex_only = re.sub(r"[:-]", "", cleaned)
    return bytes.fromhex(hex_only)


def build_magic_packet(mac: str) -> bytes:
    """Return the 102-byte magic packet for ``mac``."""
    mac_bytes = normalize_mac(mac)
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(
    mac: str,
    broadcast: str = _DEFAULT_BROADCAST,
    port: int = _DEFAULT_PORT,
    source_ip: str | None = None,
) -> None:
    """Broadcast a magic packet to wake the host with the given MAC.

    ``source_ip`` optionally binds the socket to a specific local interface address
    (the NIC that routes to the PBS subnet) so the broadcast goes out the right link.
    Raises :class:`WolError` on any socket failure.
    """
    packet = build_magic_packet(mac)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if source_ip:
                sock.bind((source_ip, 0))
            sock.sendto(packet, (broadcast, port))
    except OSError as exc:
        raise WolError(f"Failed to send Wake-on-LAN packet to {mac}: {exc}") from exc
