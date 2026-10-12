"""SSH key provisioning for the PBS power-off path.

The app owns a dedicated ed25519 key: the wizard generates it (private key stays in the
data dir, never leaves the box) and either auto-installs the public half via PBS root
(``install_public_key``) or shows it for the user to paste into ``authorized_keys``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import ssh
from .errors import PowerError, SshPasswordRefusedError
from .power import POWER_COMMANDS

_KEY_COMMENT = "joulenap"

# All Joulenap needs from PBS over SSH is to power it off or suspend it, so the key is
# installed with a forced command: sshd runs *only* this, whatever the client asks for. It
# runs the client's command when it is exactly one of POWER_COMMANDS and refuses anything
# else, so the per-device poweroff/suspend choice needs no change on the PBS. The extra
# restrictions strip port/agent/x11 forwarding and PTY allocation. So even a leaked private
# key can do nothing on the PBS but power it off or suspend it. Deliberately no hibernate and
# no user-defined command: the second would undo the whole lockdown.
#
# No double quotes inside (sshd only unescapes \"), and the space in each pattern is
# backslash-escaped for the shell, so the line needs no further quoting anywhere.
_ALLOWED = "|".join(c.replace(" ", "\\ ") for c in POWER_COMMANDS.values())
_FORCED_COMMAND = (
    f"case $SSH_ORIGINAL_COMMAND in {_ALLOWED}) exec $SSH_ORIGINAL_COMMAND;; *) exit 1;; esac"
)
_KEY_OPTIONS = (
    f'command="{_FORCED_COMMAND}",no-port-forwarding,'
    "no-x11-forwarding,no-agent-forwarding,no-pty"
)


def authorized_keys_line(public_key: str) -> str:
    """The restricted ``authorized_keys`` line for ``public_key``: the forced-command
    options prefixed to the key. This is what gets installed on the PBS (and what the
    manual-setup UI tells the user to paste), so both paths get the same lockdown."""
    return f"{_KEY_OPTIONS} {public_key.strip()}"


def _sq(s: str) -> str:
    """``s`` single-quoted for a POSIX shell."""
    return "'" + s.replace("'", "'\\''") + "'"


def install_command(public_key: str) -> str:
    """The one-line shell command that installs ``public_key`` on the PBS.

    Run by the wizard over SSH, and shown to the user to paste into the PBS web Shell when
    sshd won't take the root password. It *replaces* any line already holding this key
    rather than appending: a line installed before 1.5.0 forces a bare poweroff, and sshd
    uses the first line that matches the key, so an appended one would never take effect.
    Every other key in the file is kept.

    Written back with ``cat >`` rather than ``mv`` so a symlinked authorized_keys (a PBS
    sharing its host with PVE links it into /etc/pve) stays a symlink. grep's exit 1 means
    "no other lines", which is fine; anything above that aborts before the file is touched.
    The subshell keeps ``set -e`` from leaking into the interactive shell it is pasted into.
    """
    key = public_key.strip()
    parts = key.split()
    if len(parts) < 2:
        raise PowerError("Not an OpenSSH public key")
    blob = parts[1]  # the base64 key itself: matches our line whatever options precede it
    return (
        "(set -e; umask 077; mkdir -p ~/.ssh; chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys; "
        "t=$(mktemp); "
        f'{{ grep -vF {_sq(blob)} ~/.ssh/authorized_keys || [ $? -eq 1 ]; }} > "$t"; '
        f'echo {_sq(authorized_keys_line(key))} >> "$t"; '
        'cat "$t" > ~/.ssh/authorized_keys; rm -f "$t"; chmod 600 ~/.ssh/authorized_keys)'
    )


def public_key_from(key_path: Path, comment: str = _KEY_COMMENT) -> str:
    """Read ``key_path`` and return its OpenSSH public line, in the same form
    :func:`generate_keypair` returns.

    Raises ``OSError``/``ValueError`` if the file is missing or is not a usable private key —
    the caller decides whether that means "generate one" or "fail".
    """
    key = serialization.load_ssh_private_key(key_path.read_bytes(), password=None)
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    return f"{public_bytes.decode('ascii')} {comment}"


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
    """Install ``public_key`` (as the restricted, forced-command line) into ``user``'s
    authorized_keys on ``host`` over a password-authenticated SSH session, replacing any
    line that already holds this key (idempotent). Raises :class:`SshPasswordRefusedError`
    when sshd turns the password down, :class:`PowerError` for anything else."""
    command = install_command(public_key)
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
    except paramiko.AuthenticationException as exc:
        client.close()
        raise SshPasswordRefusedError(
            f"{user}@{host} refused password login over SSH: {exc}"
        ) from exc
    except Exception as exc:
        client.close()
        raise PowerError(f"SSH connection to {user}@{host} failed: {exc}") from exc

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
