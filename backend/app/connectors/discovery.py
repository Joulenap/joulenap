"""Wizard discovery helpers: derive the PBS connection and detect its MAC.

These turn things the app can already see (the PVE storage config, the ARP table after a
connection) into config values, so the user reviews rather than types them.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Callable

from . import net

# Default PBS API port; PVE storage config doesn't carry it.
_DEFAULT_PBS_PORT = 8007
_ARP_PATH = "/proc/net/arp"
# Short: this only has to get far enough to resolve the MAC, and a refused port answers at once.
_PRIME_TIMEOUT = 2.0
# MAC in /proc/net/arp (colon-separated only).
_MAC_RE = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
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


def match_storages_to_pbss(storages: list[dict], pbss: list) -> dict[str, str]:
    """Build a PVE's ``{pbs_device_id: pve_storage_id}`` map from its storage config.

    A storage belongs to a registered backup server when it points at the same host *and*
    the same datastore: one box can serve several datastores, and each is a separate device
    as far as routes are concerned, so the host alone is not enough. Hosts are compared
    case-insensitively and trimmed because they are free text on both sides.

    The frontend's ``matchStorage``/``linkedStorages`` do the same thing while the Add-PVE
    wizard is open; this is the half that runs later, when a backup server is added *after*
    the Proxmox host that backs up to it and the map needs filling in.
    """
    linked: dict[str, str] = {}
    for storage in storages:
        derived = derive_pbs_from_storage(storage)
        host = derived["host"].strip().lower()
        for pbs in pbss:
            if pbs.host.strip().lower() == host and pbs.datastore == derived["datastore"]:
                linked[pbs.id] = storage.get("storage", "")
                break
    return linked


def _prime_arp(ip: str, port: int) -> None:
    """Best-effort TCP connect, purely to make the kernel resolve the neighbour's MAC.

    Whether the connection succeeds is irrelevant and the result is dropped: ARP happens
    below TCP, so the kernel must learn the MAC before it can even send the SYN, and a
    refused port answers immediately. This replaced a ``ping`` subprocess, which was dead
    code in the shipped image — ``python:3.12-slim`` has no ``ping`` binary.
    """
    net.tcp_reachable(ip, port, _PRIME_TIMEOUT)


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


def detect_mac(
    host: str,
    *,
    port: int = _DEFAULT_PBS_PORT,
    prime: Callable[[str, int], None] = _prime_arp,
    read_arp_table: Callable[[], dict[str, str]] = _read_proc_arp,
    resolve: Callable[[str], str] = socket.gethostbyname,
) -> str | None:
    """Return the MAC of ``host`` (must be powered on) by connecting to it, then reading ARP.

    The PBS must be awake for this. ``port`` only decides where the priming connection goes:
    ARP is resolved below TCP, so a closed or wrong port works as well as the right one.
    Dependencies are injected so the lookup is testable without touching the network.
    Returns ``None`` if the MAC can't be found — which is always the case off Linux, since
    the cache is read from ``/proc/net/arp``. Joulenap ships as a Linux container.
    """
    try:
        ip = resolve(host)
    except OSError:
        ip = host
    prime(ip, port)
    return read_arp_table().get(ip)
