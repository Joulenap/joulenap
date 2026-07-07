"""Network helpers: TCP reachability (waiting for PBS after WoL) and local interface
enumeration + Wake-on-LAN target resolution."""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

import psutil

log = logging.getLogger("joulenap.net")

_GLOBAL_BROADCAST = "255.255.255.255"


def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """True if a TCP connection to ``host:port`` succeeds within ``timeout`` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_until_reachable(
    host: str,
    port: int,
    timeout: float,
    interval: float = 2.0,
    connect_timeout: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll ``host:port`` until reachable or ``timeout`` elapses. Returns success."""
    deadline = time.monotonic() + timeout
    while True:
        if tcp_reachable(host, port, connect_timeout):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(interval)


# --- local interfaces + Wake-on-LAN targeting --------------------------------


@dataclass(frozen=True)
class NetInterface:
    """One local NIC with an IPv4 address, and the subnet it sits on."""

    name: str
    address: str
    netmask: str

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.address}/{self.netmask}", strict=False)

    @property
    def broadcast(self) -> str:
        """The subnet-directed broadcast address for this NIC (e.g. 192.168.1.255)."""
        return str(self.network.broadcast_address)

    def contains(self, host: str) -> bool:
        """True if ``host`` (an IP) is on this NIC's subnet."""
        try:
            return ipaddress.ip_address(host) in self.network
        except ValueError:
            return False


def list_interfaces() -> list[NetInterface]:
    """Enumerate up, non-loopback NICs that have an IPv4 address."""
    stats = psutil.net_if_stats()
    out: list[NetInterface] = []
    for name, addrs in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and a.address and not a.address.startswith("127."):
                out.append(
                    NetInterface(
                        name=name,
                        address=a.address,
                        netmask=a.netmask or "255.255.255.255",
                    )
                )
    return out


def find_interface(name: str) -> NetInterface | None:
    return next((i for i in list_interfaces() if i.name == name), None)


def _resolve_ip(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def wol_target(host: str, iface_name: str = "") -> tuple[str, str | None]:
    """Resolve the magic-packet ``(destination, source_ip)`` for a configured PBS.

    Targets the PBS's **subnet-directed broadcast** — scoped to the PBS's LAN segment, not
    the whole network — binding to the chosen interface (or, if none is set, the NIC whose
    subnet contains the PBS). The packet is still L2-addressed to the PBS MAC, so only the
    PBS acts on it. If the subnet can't be determined, falls back to the PBS address itself.
    """
    ip = _resolve_ip(host)
    iface = find_interface(iface_name) if iface_name else None
    if iface_name and iface is None:
        # A NIC was explicitly configured but doesn't resolve (down/renamed). WoL failures
        # are hard to diagnose, so make the silent auto-detect fallback visible in the log.
        log.warning(
            "Configured WoL interface %r not found — falling back to auto-detection", iface_name
        )
    if iface is None and ip is not None:
        iface = next((i for i in list_interfaces() if i.contains(ip)), None)
    if iface is not None and ip is not None and iface.contains(ip):
        return iface.broadcast, iface.address
    return host, (iface.address if iface else None)
