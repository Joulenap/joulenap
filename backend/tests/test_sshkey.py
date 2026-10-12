"""SSH key hardening: the installed authorized_keys line can only power off or suspend."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from unittest import mock

import paramiko
import pytest

from app.connectors import sshkey
from app.connectors.errors import SshPasswordRefusedError
from app.connectors.sshkey import authorized_keys_line, install_command, install_public_key

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA joulenap"


def test_authorized_keys_line_forces_the_dispatcher_and_strips_forwarding():
    line = authorized_keys_line(PUBKEY)
    assert line.endswith(" " + PUBKEY)
    assert line.startswith('command="case $SSH_ORIGINAL_COMMAND in ')
    # sshd only unescapes a backslash-quote inside the option, so a stray quote would end it.
    assert line.count('"') == 2
    for opt in ("no-port-forwarding", "no-x11-forwarding", "no-agent-forwarding", "no-pty"):
        assert opt in line


def test_authorized_keys_line_is_single_line():
    assert "\n" not in authorized_keys_line(PUBKEY + "\n")


def _mock_client(exit_status: int = 0):
    client = mock.MagicMock()
    stdout = mock.MagicMock()
    stdout.channel.recv_exit_status.return_value = exit_status
    client.exec_command.return_value = (mock.MagicMock(), stdout, mock.MagicMock())
    return client


def test_install_appends_restricted_line_not_bare_key():
    client = _mock_client()
    with mock.patch("paramiko.SSHClient", return_value=client):
        install_public_key("pbs.local", "root", "pw", PUBKEY)
    remote_cmd = client.exec_command.call_args.args[0]
    # The installer runs exactly the command the wizard shows for pasting, so what lands in
    # authorized_keys is the restricted line and both paths stay identical.
    assert remote_cmd == install_command(PUBKEY)
    assert authorized_keys_line(PUBKEY) in remote_cmd


def test_install_reports_a_refused_password_as_its_own_error(monkeypatch):
    client = mock.MagicMock()
    client.connect.side_effect = paramiko.BadAuthenticationType("denied", ["publickey"])
    monkeypatch.setattr(sshkey.ssh, "strict_client", lambda: client)
    with pytest.raises(SshPasswordRefusedError):
        install_public_key("pbs.local", "root", "pw", PUBKEY)
    client.close.assert_called_once()
    client.exec_command.assert_not_called()


def test_install_command_rejects_something_that_is_not_a_public_key():
    with pytest.raises(sshkey.PowerError):
        install_command("garbage")


# --- the shell side, run for real --------------------------------------------
# A forced command and an authorized_keys rewrite are shell, so string assertions can't tell
# whether they work. These run them under sh, the way sshd and the PBS Shell would.

_SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(_SH is None or os.name == "nt", reason="needs a POSIX sh")


def _forced(original: str | None) -> subprocess.CompletedProcess:
    """Run the line's forced command as sshd would, with ``exec`` swapped for ``echo`` so an
    accepted command is printed instead of powering anything off."""
    line = authorized_keys_line(PUBKEY)
    forced = re.match(r'command="(.*?)",no-port', line).group(1).replace("exec ", "echo ")
    env = {"PATH": os.environ.get("PATH", "")}
    if original is not None:
        env["SSH_ORIGINAL_COMMAND"] = original
    return subprocess.run([_SH, "-c", forced], env=env, capture_output=True, text=True)


@needs_sh
@pytest.mark.parametrize("command", ["systemctl poweroff", "systemctl suspend"])
def test_forced_command_runs_the_two_power_commands(command):
    result = _forced(command)
    assert result.returncode == 0
    assert result.stdout.strip() == command


@needs_sh
@pytest.mark.parametrize(
    "command",
    [
        None,  # an interactive login: no command at all
        "",
        "systemctl reboot",
        "systemctl hibernate",
        "systemctl poweroff; rm -rf /",
        "systemctl suspend && id",
        "systemctl  poweroff",
        "sh",
    ],
)
def test_forced_command_refuses_everything_else(command):
    result = _forced(command)
    assert result.returncode != 0
    assert result.stdout == ""


OTHER = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ someone@laptop"


def _run_install(home) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
    subprocess.run([_SH, "-c", install_command(PUBKEY)], env=env, check=True)


def _install_into(home, existing: str | None) -> list[str]:
    ssh_dir = home / ".ssh"
    if existing is not None:
        ssh_dir.mkdir(mode=0o700)
        (ssh_dir / "authorized_keys").write_text(existing)
    _run_install(home)
    return (ssh_dir / "authorized_keys").read_text().splitlines()


@needs_sh
def test_install_command_creates_the_file(tmp_path):
    assert _install_into(tmp_path, None) == [authorized_keys_line(PUBKEY)]
    assert (tmp_path / ".ssh" / "authorized_keys").stat().st_mode & 0o777 == 0o600


@needs_sh
def test_install_command_replaces_a_pre_1_5_line_and_keeps_other_keys(tmp_path):
    old = f'command="systemctl poweroff",no-port-forwarding,no-pty {PUBKEY}'
    lines = _install_into(tmp_path, f"{OTHER}\n{old}\n")
    # The old line would win (sshd takes the first match), so it must be gone, not shadowed.
    assert lines == [OTHER, authorized_keys_line(PUBKEY)]


@needs_sh
def test_install_command_is_idempotent(tmp_path):
    _install_into(tmp_path, f"{OTHER}\n")
    _run_install(tmp_path)
    lines = (tmp_path / ".ssh" / "authorized_keys").read_text().splitlines()
    assert lines == [OTHER, authorized_keys_line(PUBKEY)]


@needs_sh
def test_install_command_follows_a_symlinked_authorized_keys(tmp_path):
    # A PBS sharing its host with PVE links authorized_keys into /etc/pve: keep the link.
    target = tmp_path / "shared_keys"
    target.write_text(f"{OTHER}\n")
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    (ssh_dir / "authorized_keys").symlink_to(target)
    _run_install(tmp_path)
    assert (ssh_dir / "authorized_keys").is_symlink()
    assert target.read_text().splitlines() == [OTHER, authorized_keys_line(PUBKEY)]


@needs_sh
def test_install_command_does_not_leak_set_e_into_the_pasting_shell(tmp_path):
    # Pasted into an interactive shell: a later failing command must not close it.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    script = install_command(PUBKEY) + "; false; echo still-here"
    result = subprocess.run([_SH, "-c", script], env=env, capture_output=True, text=True)
    assert "still-here" in result.stdout


def test_install_uses_strict_client(monkeypatch):
    used = {}

    class FakeClient:
        def set_missing_host_key_policy(self, p):
            used["policy"] = type(p).__name__

        def connect(self, **kw):
            used["connected"] = True

        def exec_command(self, cmd, timeout=None):
            class Ch:
                def recv_exit_status(self):
                    return 0

            class Out:
                channel = Ch()

                def read(self):
                    return b""

            return None, Out(), Out()

        def close(self):
            pass

    monkeypatch.setattr(sshkey.ssh, "strict_client", lambda: FakeClient())
    sshkey.install_public_key("h", "root", "pw", "ssh-ed25519 AAAA key")
    assert used["connected"]
