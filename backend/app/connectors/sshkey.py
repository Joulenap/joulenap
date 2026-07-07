"""SSH key provisioning for the PBS power-off path.

The app owns a dedicated ed25519 key: the wizard generates it (private key stays in the
data dir, never leaves the box) and either auto-installs the public half via PBS root
(``install_public_key``) or shows it for the user to paste into ``authorized_keys``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import ssh
from .errors import PowerError
from .power import POWEROFF_COMMAND

_KEY_COMMENT = "joulenap"

# The one thing Joulenap needs from PBS over SSH is a shutdown, so the key is installed
# with a forced command: the PBS runs *only* this, ignoring whatever the client asks for,
# and the extra restrictions strip port/agent/x11 forwarding and PTY allocation. So even if
# the private key leaked, it could do nothing on the PBS but power it off. Surfaced in the
# wizard as the trust story; also used for the manual-paste authorized_keys line.
_FORCED_COMMAND = POWEROFF_COMMAND
_KEY_OPTIONS = (
    f'command="{_FORCED_COMMAND}",no-port-forwarding,'
    "no-x11-forwarding,no-agent-forwarding,no-pty"
)


def authorized_keys_line(public_key: str) -> str:
    """The restricted ``authorized_keys`` line for ``public_key`` — the forced-poweroff
    options prefixed to the key. This is what gets installed on the PBS (and what the
    manual-setup UI tells the user to paste), so both paths get the same lockdown."""
    return f"{_KEY_OPTIONS} {public_key.strip()}"


def generate_keypair(key_path: Path, comment: str = _KEY_COMMENT) -> str:
    """Generate an ed25519 keypair, write the private key to ``key_path`` (mode 0600),
    and return the OpenSSH public key line. Overwrites any existing key at that path."""
    key = Ed25519PrivateKey.generate()
    private_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )

    key_path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive perms from the start so the private key is never world-readable.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as fh:
        fh.write(private_bytes)
        if not private_bytes.endswith(b"\n"):
            fh.write(b"\n")

    return f"{public_bytes.decode('ascii')} {comment}"


def install_public_key(
    host: str,
    user: str,
    password: str,
    public_key: str,
    port: int = 22,
    timeout: float = 10.0,
) -> None:
    """Append ``public_key`` (as the restricted, forced-poweroff line) to ``user``'s
    authorized_keys on ``host`` over a password-authenticated SSH session (idempotent).
    Raises :class:`PowerError`."""
    client = ssh.strict_client()
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except Exception as exc:
        client.close()
        raise PowerError(f"SSH connection to {user}@{host} failed: {exc}") from exc

    # Install the restricted line (forced poweroff command + no forwarding), not the bare
    # key, so the installed access can only shut the PBS down. Single-quote for the remote
    # shell; append only if not already present.
    safe_key = authorized_keys_line(public_key).replace("'", "'\\''")
    command = (
        "set -e; mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys "
        "&& chmod 600 ~/.ssh/authorized_keys "
        f"&& grep -qxF '{safe_key}' ~/.ssh/authorized_keys "
        f"|| echo '{safe_key}' >> ~/.ssh/authorized_keys"
    )
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = stderr.read().decode("utf-8", "replace").strip()
            raise PowerError(f"Installing the SSH key on {host} failed: {err or exit_status}")
    except PowerError:
        raise
    except Exception as exc:
        raise PowerError(f"Installing the SSH key on {host} failed: {exc}") from exc
    finally:
        client.close()
