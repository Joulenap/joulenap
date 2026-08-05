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


# --- matching discovered storages back onto registered devices ----------------


class _Pbs:
    """Just the three fields the matcher reads off a PbsDevice."""

    def __init__(self, id: str, host: str, datastore: str):
        self.id = id
        self.host = host
        self.datastore = datastore


def _storage(name: str, server: str, datastore: str) -> dict:
    return {"storage": name, "server": server, "datastore": datastore, "fingerprint": ""}


def test_match_links_a_storage_to_the_device_it_points_at():
    linked = discovery.match_storages_to_pbss(
        [_storage("pbs", "192.0.2.20", "backup"), _storage("pbs2", "192.0.2.21", "lab")],
        [_Pbs("pbs-01", "192.0.2.20", "backup"), _Pbs("pbs-2", "192.0.2.21", "lab")],
    )
    assert linked == {"pbs-01": "pbs", "pbs-2": "pbs2"}


def test_match_needs_the_datastore_too_not_just_the_host():
    """One box can serve several datastores, and each is a separate device as far as routes
    are concerned — so the host alone must not be enough to claim a storage."""
    linked = discovery.match_storages_to_pbss(
        [_storage("pbs", "192.0.2.20", "backup")],
        [_Pbs("pbs-other", "192.0.2.20", "archive")],
    )
    assert linked == {}


def test_match_ignores_host_case_and_padding():
    linked = discovery.match_storages_to_pbss(
        [_storage("pbs", " PBS.Local ", "backup")], [_Pbs("pbs-01", "pbs.local", "backup")]
    )
    assert linked == {"pbs-01": "pbs"}


def test_match_skips_storages_no_registered_device_claims():
    linked = discovery.match_storages_to_pbss(
        [_storage("stranger", "192.0.2.99", "backup")], [_Pbs("pbs-01", "192.0.2.20", "backup")]
    )
    assert linked == {}
