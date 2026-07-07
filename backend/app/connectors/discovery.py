"""Wizard discovery helpers: derive the PBS connection and detect its MAC.

These turn things the app can already see (the PVE storage config, the ARP table after a
ping) into config values, so the user reviews rather than types them.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
from collections.abc import Callable

# Default PBS API port; PVE storage config doesn't carry it.
_DEFAULT_PBS_PORT = 8007
_ARP_PATH = "/proc/net/arp"
_IS_WINDOWS = sys.platform.startswith("win")
# MAC in /proc/net/arp (colon-separated only).
_MAC_RE = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
# MAC in `arp -a` output: colon- (Unix) or dash-separated (Windows).
_MAC_TOKEN_RE = re.compile(r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})")
_IP_TOKEN_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")
# Incomplete ARP entries carry an all-zero HW address that still matches the MAC regex;
# saving it would break WoL silently, so treat it as "no MAC found".
_ZERO_MAC = "00:00:00:00:00:00"


def derive_pbs_from_storage(storage: dict) -> dict:
    """Map a PVE ``type=pbs`` storage config to Joulenap's ``pbs`` connection fields.

    PVE stores the PBS host as ``server`` plus ``datastore`` and ``fingerprint``; the
    API port isn't stored, so we use the well-known default.
    """
    return {
        "host": storage.get("server", ""),
        "port": _DEFAULT_PBS_PORT,
        "datastore": storage.get("datastore", ""),
        "fingerprint": storage.get("fingerprint", ""),
    }


def _ping(host: str, timeout: float = 1.0) -> None:
    """Best-effort single ping to populate the ARP cache (Windows vs Unix flags differ)."""
    if _IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(max(timeout, 1) * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(int(max(timeout, 1))), host]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout + 2, check=False)
    except (OSError, subprocess.SubprocessError):
        pass  # a failed ping is fine — the neighbour may already be in the ARP table


def _read_proc_arp() -> dict[str, str]:
    """Parse ``/proc/net/arp`` into an ``{ip: mac}`` map (Linux only)."""
    table: dict[str, str] = {}
    try:
        with open(_ARP_PATH, encoding="ascii") as fh:
            next(fh, None)  # header row
            for line in fh:
                # Columns: IP, HW type, Flags, HW address, Mask, Device. Flags 0x0 marks an
                # incomplete entry (no real HW address yet) — skip it.
                fields = line.split()
                if len(fields) >= 4 and fields[2] != "0x0" and _MAC_RE.fullmatch(fields[3]):
                    mac = fields[3].lower()
                    if mac != _ZERO_MAC:
                        table[fields[0]] = mac
    except OSError:
        pass
    return table


def _read_arp_command() -> dict[str, str]:
    """Parse ``arp -a`` output into an ``{ip: mac}`` map. Portable (Windows + Unix); MACs
    are normalised to lower-case colon form. Lines without both an IP and a MAC (e.g. the
    Windows ``Interface:`` headers) are skipped."""
    table: dict[str, str] = {}
    try:
        out = subprocess.run(
            ["arp", "-a"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return table
    for line in out.splitlines():
        ip_match = _IP_TOKEN_RE.search(line)
        mac_match = _MAC_TOKEN_RE.search(line)
        if ip_match and mac_match:
            mac = mac_match.group(1).replace("-", ":").lower()
            if mac != _ZERO_MAC:
                table[ip_match.group(1)] = mac
    return table


def _read_arp_table() -> dict[str, str]:
    """Read the system ARP cache as ``{ip: mac}``, by the best route for the platform:
    ``/proc/net/arp`` on Linux (with an ``arp -a`` fallback), ``arp -a`` on Windows."""
    if _IS_WINDOWS:
        return _read_arp_command()
    return _read_proc_arp() or _read_arp_command()


def detect_mac(
    host: str,
    *,
    ping: Callable[[str], None] = _ping,
    read_arp_table: Callable[[], dict[str, str]] = _read_arp_table,
    resolve: Callable[[str], str] = socket.gethostbyname,
) -> str | None:
    """Return the MAC of ``host`` (must be powered on) by pinging then reading ARP.

    The PBS must be awake for this. Dependencies are injected so the lookup is testable
    without touching the network. Returns ``None`` if the MAC can't be found.
    """
    try:
        ip = resolve(host)
    except OSError:
        ip = host
    ping(ip)
    return read_arp_table().get(ip)
