"""Wizard discovery helpers: PBS derivation, MAC detection, SSH keygen."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from app.connectors import discovery
from app.connectors.discovery import derive_pbs_from_storage, detect_mac
from app.connectors.sshkey import generate_keypair


def test_derive_pbs_from_storage():
    derived = derive_pbs_from_storage(
        {"server": "192.168.1.5", "datastore": "backup", "fingerprint": "AA:BB:CC"}
    )
    assert derived == {
        "host": "192.168.1.5",
        "port": 8007,
        "datastore": "backup",
        "fingerprint": "AA:BB:CC",
    }


def test_derive_pbs_from_storage_missing_fields():
    assert derive_pbs_from_storage({}) == {
        "host": "",
        "port": 8007,
        "datastore": "",
        "fingerprint": "",
    }


def test_detect_mac_found():
    pings: list[str] = []
    mac = detect_mac(
        "pbs.local",
        ping=pings.append,
        read_arp_table=lambda: {"10.0.0.5": "00:11:22:33:44:55"},
        resolve=lambda _h: "10.0.0.5",
    )
    assert mac == "00:11:22:33:44:55"
    assert pings == ["10.0.0.5"]  # pings the resolved IP


def test_detect_mac_not_in_table():
    mac = detect_mac(
        "pbs.local",
        ping=lambda _h: None,
        read_arp_table=lambda: {},
        resolve=lambda _h: "10.0.0.5",
    )
    assert mac is None


_WINDOWS_ARP = """
Interface: 192.0.2.21 --- 0x3
  Internet Address      Physical Address      Type
  192.0.2.1         aa-11-bb-22-cc-33     dynamic
  192.0.2.213       00-11-22-33-44-55     dynamic
  255.255.255.255       ff-ff-ff-ff-ff-ff     static
"""

_UNIX_ARP = "pbs (192.0.2.213) at 00:11:22:33:44:55 [ether] on eth0\n"


def test_read_arp_command_parses_windows_output():
    completed = mock.Mock(stdout=_WINDOWS_ARP)
    with mock.patch("subprocess.run", return_value=completed):
        table = discovery._read_arp_command()
    # Dash-separated MACs are normalised to lower-case colon form.
    assert table["192.0.2.213"] == "00:11:22:33:44:55"
    assert table["192.0.2.1"] == "aa:11:bb:22:cc:33"


def test_read_arp_command_parses_unix_output():
    completed = mock.Mock(stdout=_UNIX_ARP)
    with mock.patch("subprocess.run", return_value=completed):
        table = discovery._read_arp_command()
    assert table == {"192.0.2.213": "00:11:22:33:44:55"}


def test_generate_keypair_writes_private_and_returns_public(tmp_path: Path):
    key_path = tmp_path / "id_ed25519"
    public = generate_keypair(key_path)

    assert public.startswith("ssh-ed25519 ")
    assert public.strip().endswith("joulenap")
    assert key_path.exists()
    body = key_path.read_text()
    assert body.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
