"""Persisted SSH host-key trust (TOFU) for the PBS power-off connection.

Instead of trusting whatever host key appears on every connect (paramiko AutoAddPolicy),
the wizard confirms the PBS host key once and saves it to ``data/known_hosts``; thereafter
every connection verifies against that file (RejectPolicy). No new dependency.
"""

from __future__ import annotations

import base64
import hashlib
import socket
from collections.abc import Callable
from pathlib import Path

import paramiko

from .. import paths


def _path(path: Path | None) -> Path:
    return path or paths.known_hosts_path()


def _fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _default_connect(host: str, port: int, timeout: float) -> paramiko.Transport:
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    transport.start_client(timeout=timeout)
    return transport


def scan_host_key(
    host: str,
    port: int = 22,
    timeout: float = 10.0,
    *,
    connect: Callable[[str, int, float], paramiko.Transport] = _default_connect,
) -> tuple[str, str, str]:
    """Open an unauthenticated SSH transport, read the server's host key, and return
    ``(key_type, key_base64, fingerprint)``. Does not authenticate."""
    transport = connect(host, port, timeout)
    try:
        key = transport.get_remote_server_key()
        return key.get_name(), key.get_base64(), _fingerprint(key)
    finally:
        transport.close()


def save_host_key(
    host: str, key_type: str, key_base64: str, *, port: int = 22, path: Path | None = None
) -> None:
    """Add/replace ``host``'s entry in the known_hosts file (idempotent)."""
    p = _path(path)
    key = paramiko.PKey.from_type_string(key_type, base64.b64decode(key_base64))
    hostkeys = paramiko.HostKeys()
    if p.exists():
        hostkeys.load(str(p))
    entry = host if port == 22 else f"[{host}]:{port}"
    hostkeys.add(entry, key_type, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    hostkeys.save(str(p))


def host_key_known(host: str, *, port: int = 22, path: Path | None = None) -> bool:
    p = _path(path)
    if not p.exists():
        return False
    hostkeys = paramiko.HostKeys()
    hostkeys.load(str(p))
    entry = host if port == 22 else f"[{host}]:{port}"
    return hostkeys.lookup(entry) is not None


def strict_client(path: Path | None = None) -> paramiko.SSHClient:
    """An SSHClient that loads the persisted known_hosts (if any) and rejects unknown keys."""
    client = paramiko.SSHClient()
    p = _path(path)
    if p.exists():
        client.load_host_keys(str(p))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client
