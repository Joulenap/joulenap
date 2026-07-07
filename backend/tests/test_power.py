"""SSH power connector — paramiko mocked, no real network."""

from __future__ import annotations

from unittest import mock

import pytest

from app.connectors import power
from app.connectors.errors import PowerError
from app.connectors.power import PbsPower


@pytest.fixture(autouse=True)
def _known_host_key(monkeypatch):
    """Default the host key to "already trusted" so these tests exercise the connect
    path only, without falling into the TOFU-scan branch (which would otherwise hit
    the real network since no known_hosts/data-dir fixture is set up here)."""
    monkeypatch.setattr(power.ssh, "host_key_known", lambda host, port=22: True)


def _mock_client(exit_status: int = 0):
    """Build a fake SSHClient whose exec_command returns the given exit status."""
    client = mock.MagicMock()
    stdout = mock.MagicMock()
    stdout.channel.recv_exit_status.return_value = exit_status
    client.exec_command.return_value = (mock.MagicMock(), stdout, mock.MagicMock())
    return client


def test_test_connection_ok():
    client = _mock_client(exit_status=0)
    with mock.patch("paramiko.SSHClient", return_value=client):
        PbsPower("10.0.0.12", user="root", key_path="/k").test_connection()
    client.connect.assert_called_once()
    kwargs = client.connect.call_args.kwargs
    assert kwargs["hostname"] == "10.0.0.12"
    assert kwargs["username"] == "root"
    assert kwargs["key_filename"] == "/k"
    assert kwargs["look_for_keys"] is False
    client.close.assert_called_once()


def test_test_connection_does_not_exec():
    """The installed key forces a poweroff command, so a test must never run anything —
    authenticating (connect) is the whole check."""
    client = _mock_client(exit_status=0)
    with mock.patch("paramiko.SSHClient", return_value=client):
        PbsPower("10.0.0.12").test_connection()
    client.exec_command.assert_not_called()
    client.close.assert_called_once()


def test_connect_failure_raises_and_closes():
    client = mock.MagicMock()
    client.connect.side_effect = OSError("refused")
    with mock.patch("paramiko.SSHClient", return_value=client):
        with pytest.raises(PowerError):
            PbsPower("10.0.0.12").test_connection()
    client.close.assert_called_once()


def test_poweroff_runs_command():
    client = _mock_client()
    with mock.patch("paramiko.SSHClient", return_value=client):
        PbsPower("10.0.0.12", poweroff_command="systemctl poweroff").poweroff()
    cmd = client.exec_command.call_args.args[0]
    assert cmd == "systemctl poweroff"
    client.close.assert_called_once()


def test_connect_uses_strict_client(monkeypatch, tmp_path):
    used = {}

    class FakeClient:
        def __init__(self):
            used["made"] = True

        def load_system_host_keys(self):
            pass

        def connect(self, **kw):
            used["connected"] = True

        def close(self):
            pass

        def get_transport(self):
            return None

    monkeypatch.setattr(power.ssh, "strict_client", lambda: FakeClient())
    monkeypatch.setattr(power.ssh, "host_key_known", lambda host, port=22: True)
    p = power.PbsPower(host="pbs.local", key_path=str(tmp_path / "k"))
    client = p._connect()
    assert used["connected"] and used["made"]
    client.close()
