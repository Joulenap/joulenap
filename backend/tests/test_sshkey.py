"""SSH key hardening: the installed authorized_keys line is locked to poweroff only."""

from __future__ import annotations

from unittest import mock

from app.connectors.sshkey import authorized_keys_line, install_public_key

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA joulenap"


def test_authorized_keys_line_forces_poweroff_and_strips_forwarding():
    line = authorized_keys_line(PUBKEY)
    assert line.endswith(" " + PUBKEY)
    assert line.startswith('command="systemctl poweroff",')
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
    # The command the installer runs must embed the forced-command line, so what lands in
    # authorized_keys can only power the PBS off.
    assert 'command="systemctl poweroff"' in remote_cmd
    assert "no-pty" in remote_cmd
