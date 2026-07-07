"""Setup-wizard discovery + provisioning orchestration (see docs/CONFIG-WIZARD.md).

Stateless helpers: each takes the connection params it needs and returns discovered
values (and, in quick setup, a freshly created token). Nothing is persisted here — the
frontend assembles the result and saves it once via ``PUT /api/config``. The one
exception is ``ssh_keygen``, which must write the private key to disk.

Connector classes are referenced at module scope so tests can patch them.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import httpx

from ..connectors import net, tls
from ..connectors.discovery import derive_pbs_from_storage, detect_mac
from ..connectors.errors import ApiError
from ..connectors.pbs import get_fingerprint
from ..connectors.provision import PbsProvisioner, PveProvisioner
from ..connectors.pve import PveClient
from ..connectors.sshkey import authorized_keys_line, generate_keypair, install_public_key

_PBS_PROBE_TIMEOUT = 3.0


def pve_connect(
    *,
    host: str,
    port: int = 8006,
    verify_tls: bool = False,
    mode: str = "token",
    token_id: str | None = None,
    token_secret: str | None = None,
    username: str | None = None,
    password: str | None = None,
    token_name: str = "joulenap",
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Validate the PVE connection and discover nodes + PBS storages.

    In ``root`` mode the password is used once to create a scoped token (returned under
    ``token`` so the caller can persist it) and then discarded.
    """
    created: dict[str, str] | None = None
    if mode == "root":
        if not username or not password:
            raise ApiError("Root mode requires a username and password")
        with PveProvisioner(host, port, verify_tls, transport=transport) as prov:
            token = prov.provision_token(username, password, token_name)
        token_id, token_secret = token.token_id, token.secret
        created = {"id": token.token_id, "secret": token.secret}
    elif not token_id or not token_secret:
        raise ApiError("Token mode requires api_token_id and api_token_secret")

    with PveClient(
        host=host,
        node="",
        token_id=token_id,
        token_secret=token_secret,
        port=port,
        verify_tls=verify_tls,
        transport=transport,
    ) as pve:
        version = pve.version()
        nodes = [{"node": n.get("node", ""), "status": n.get("status")} for n in pve.list_nodes()]
        storages = [
            {"storage": s.get("storage", ""), **derive_pbs_from_storage(s)}
            for s in pve.list_pbs_storages()
        ]

    return {
        "connected": True,
        "version": version.get("version") if isinstance(version, dict) else None,
        "nodes": nodes,
        "storages": storages,
        "token": created,
    }


def storage_derive(
    *,
    host: str,
    port: int,
    verify_tls: bool,
    token_id: str,
    token_secret: str,
    storage_id: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Read one PVE storage config and derive the PBS connection fields from it."""
    with PveClient(
        host=host,
        node="",
        token_id=token_id,
        token_secret=token_secret,
        port=port,
        verify_tls=verify_tls,
        transport=transport,
    ) as pve:
        config = pve.get_storage(storage_id)
    return derive_pbs_from_storage(config)


def pbs_provision(
    *,
    host: str,
    port: int = 8007,
    verify_tls: bool = False,
    username: str,
    password: str,
    datastore: str,
    token_name: str = "joulenap",
    fingerprint: str = "",
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Quick-setup: create a scoped PBS API token from root creds and return it.

    The password is used once here and discarded; the caller persists the returned token.
    A realm-less username (e.g. ``root``, the SSH default) is assumed to be ``@pam``.
    """
    userid = username if "@" in username else f"{username}@pam"
    verify: bool | ssl.SSLContext = verify_tls
    if fingerprint and transport is None:  # pin when known (skip in tests using a transport)
        verify = tls.pinned_ssl_context(host, port, fingerprint)
    with PbsProvisioner(host, port, verify, transport=transport) as prov:
        token = prov.provision_token(userid, password, datastore, token_name)
    return {"id": token.token_id, "secret": token.secret}


def pbs_check(*, host: str, port: int = 8007) -> dict[str, Any]:
    """Probe PBS reachability and read its TLS fingerprint."""
    reachable = net.tcp_reachable(host, port, _PBS_PROBE_TIMEOUT)
    fingerprint: str | None = None
    try:
        fingerprint = get_fingerprint(host, port)
    except ApiError:
        fingerprint = None
    return {"reachable": reachable, "fingerprint": fingerprint}


def wol_detect_mac(*, host: str) -> dict[str, Any]:
    """Detect the MAC of a powered-on PBS via ping + ARP."""
    return {"mac": detect_mac(host)}


def ssh_keygen(*, key_path: Path) -> dict[str, Any]:
    """Generate the app's ed25519 keypair; return the public key, the restricted
    authorized_keys line to install (forced poweroff command), and where it was written."""
    public_key = generate_keypair(key_path)
    return {
        "public_key": public_key,
        "authorized_keys_line": authorized_keys_line(public_key),
        "key_path": str(key_path),
    }


def ssh_install(
    *, host: str, user: str, password: str, public_key: str, port: int = 22
) -> dict[str, Any]:
    """Install the public key into the PBS user's authorized_keys over SSH."""
    install_public_key(host, user, password, public_key, port)
    return {"installed": True}
