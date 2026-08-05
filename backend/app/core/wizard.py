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
from cryptography.exceptions import UnsupportedAlgorithm

from ..connectors import net, ssh, tls
from ..connectors.discovery import derive_pbs_from_storage, detect_mac
from ..connectors.errors import ApiError
from ..connectors.pbs import get_fingerprint
from ..connectors.provision import PBS_REMOTE_ROLES, PbsProvisioner, PveProvisioner
from ..connectors.pve import PveClient
from ..connectors.sshkey import (
    authorized_keys_line,
    generate_keypair,
    install_public_key,
    public_key_from,
)
from ..connectors.wol import send_magic_packet

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


def _pbs_root_verify(
    host: str, port: int, verify_tls: bool, fingerprint: str, transport: object | None
) -> bool | ssl.SSLContext:
    """The TLS setting for a PBS call that carries a **root password**.

    A fingerprint pins the connection. Without one — and without ``verify_tls`` — the
    password would go out over a connection nothing has authenticated, so this refuses
    instead. That is the one case where failing is friendlier than proceeding: the wizard
    captures a fingerprint one step earlier, so an empty one here means something is wrong
    with the setup rather than that the user opted out.

    ``transport`` is the test seam: an injected transport never opens a socket.
    """
    if transport is not None:
        return verify_tls
    if fingerprint:
        return tls.pinned_ssl_context(host, port, fingerprint)
    if not verify_tls:
        raise ApiError(
            f"Refusing to send root credentials to {host}:{port} over an unverified "
            "connection: store the PBS certificate fingerprint first (the wizard's connect "
            "step reads it), or enable TLS verification for this device."
        )
    return True


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
    verify = _pbs_root_verify(host, port, verify_tls, fingerprint, transport)
    with PbsProvisioner(host, port, verify, transport=transport) as prov:
        token = prov.provision_token(userid, password, datastore, token_name)
    return {"id": token.token_id, "secret": token.secret}


def pbs_grant_sync(
    *,
    host: str,
    port: int = 8007,
    verify_tls: bool = False,
    username: str,
    password: str,
    token_id: str,
    fingerprint: str = "",
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Add the ``/remote`` roles a Sync route needs to a token that already exists.

    For a PBS whose token predates those grants — provisioned by 0.9's wizard, or pasted by
    hand. PBS answers *400 Unprivileged API tokens can't set ACL items* to any token, so
    this needs a real root login; the password is used once here and discarded, exactly as
    in :func:`pbs_provision`. The token itself is left alone: only its ACL changes.
    """
    userid = username if "@" in username else f"{username}@pam"
    verify = _pbs_root_verify(host, port, verify_tls, fingerprint, transport)
    with PbsProvisioner(host, port, verify, transport=transport) as prov:
        prov.login(userid, password)
        for role in PBS_REMOTE_ROLES:
            prov.grant_acl(token_id, "/remote", role)
    return {"token_id": token_id, "roles": list(PBS_REMOTE_ROLES)}


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


def wol_test(*, mac: str, host: str = "", iface: str = "") -> dict[str, Any]:
    """Send one magic packet, to check the MAC and the network path before saving a device.

    With a ``host`` the packet is scoped to that subnet's directed broadcast and sent from
    the chosen (or auto-detected) interface, rather than blasting the whole network. Without
    one — pre-setup, when the target subnet is still unknown — it falls back to the global
    broadcast. "Sent" only means the packet left; whether the box wakes is what the user
    watches for.
    """
    if host:
        dest, source_ip = net.wol_target(host, iface)
    else:
        dest, source_ip = "255.255.255.255", None
    send_magic_packet(mac, broadcast=dest, source_ip=source_ip)
    return {"sent": True, "mac": mac, "broadcast": dest}


def ssh_keygen(*, key_path: Path) -> dict[str, Any]:
    """Return the app's ed25519 keypair, generating it only if there isn't one already.

    Get-or-create, not create: every PbsDevice's ``ssh_key_path`` defaults to this one file, so
    regenerating on the second wizard run would leave the *first* PBS with an authorized_keys
    line no private key matches any more — power-off would fail on a box that had been working,
    with nothing in the UI to say why. A file that exists but can't be parsed is replaced: it
    can't be the key any PBS trusts either, so there is nothing to preserve.

    Returns the public key, the restricted authorized_keys line to install (forced poweroff
    command), where it lives, and whether this call created it.
    """
    try:
        return _key_result(public_key_from(key_path), key_path, created=False)
    except (OSError, ValueError, UnsupportedAlgorithm):
        return _key_result(generate_keypair(key_path), key_path, created=True)


def _key_result(public_key: str, key_path: Path, *, created: bool) -> dict[str, Any]:
    return {
        "public_key": public_key,
        "authorized_keys_line": authorized_keys_line(public_key),
        "key_path": str(key_path),
        "created": created,
    }


def ssh_install(
    *, host: str, user: str, password: str, public_key: str, port: int = 22
) -> dict[str, Any]:
    """Install the public key into the PBS user's authorized_keys over SSH."""
    install_public_key(host, user, password, public_key, port)
    return {"installed": True}


def ssh_hostkey(*, host: str, port: int = 22) -> dict[str, Any]:
    """Scan the PBS SSH host key so the user can confirm its fingerprint before we send the
    root password. Returns the key material + a SHA256 fingerprint."""
    key_type, key_base64, fingerprint = ssh.scan_host_key(host, port)
    return {"key_type": key_type, "key_base64": key_base64, "fingerprint": fingerprint}


def ssh_trust(*, host: str, key_type: str, key_base64: str, port: int = 22) -> dict[str, Any]:
    """Persist a user-confirmed PBS host key to known_hosts (so later connections verify it)."""
    ssh.save_host_key(host, key_type, key_base64, port=port)
    return {"trusted": True}
