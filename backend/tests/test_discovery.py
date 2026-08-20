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
    primed: list[tuple[str, int]] = []
    mac = detect_mac(
        "pbs.local",
        prime=lambda ip, port: primed.append((ip, port)),
        read_arp_table=lambda: {"10.0.0.5": "00:11:22:33:44:55"},
        resolve=lambda _h: "10.0.0.5",
    )
    assert mac == "00:11:22:33:44:55"
    # Connects to the resolved IP, on the default PBS port unless told otherwise: that
    # connection is the only thing that puts the neighbour in the ARP cache.
    assert primed == [("10.0.0.5", 8007)]


def test_detect_mac_primes_arp_with_a_real_connect():
    """The priming step must be a socket connect, not a subprocess.

    It used to shell out to ``ping``, which does not exist in the shipped
    ``python:3.12-slim`` image — the failure was swallowed, so the cache was never
    populated and the lookup only ever succeeded on traffic somebody else had made.
    """
    with mock.patch("app.connectors.net.socket.create_connection") as connect:
        discovery._prime_arp("10.0.0.5", 8007)
    assert connect.call_args.args[0] == ("10.0.0.5", 8007)


def test_detect_mac_primes_even_when_the_port_refuses():
    # A refused port still resolves the MAC: ARP happens below TCP.
    with mock.patch("app.connectors.net.socket.create_connection", side_effect=OSError):
        discovery._prime_arp("10.0.0.5", 8007)  # must not raise


def test_detect_mac_not_in_table():
    mac = detect_mac(
        "pbs.local",
        prime=lambda _ip, _port: None,
        read_arp_table=lambda: {},
        resolve=lambda _h: "10.0.0.5",
    )
    assert mac is None


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


# --- the real /proc/net/arp parser -------------------------------------------
#
# detect_mac's tests inject `read_arp_table`, so the default implementation behind that
# seam had never run. It is the half that actually decides whether Wake-on-LAN can be set
# up from the wizard, and it fails by returning {} rather than by raising.


def _arp_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "arp"
    path.write_text(
        "IP address       HW type     Flags       HW address            Mask     Device\n" + body,
        encoding="ascii",
    )
    return path


def test_read_proc_arp_parses_a_real_table(tmp_path: Path, monkeypatch):
    table = _arp_file(
        tmp_path,
        "192.168.1.20     0x1         0x2         AA:BB:CC:DD:EE:FF     *        eth0\n"
        "192.168.1.21     0x1         0x2         11:22:33:44:55:66     *        eth0\n",
    )
    monkeypatch.setattr(discovery, "_ARP_PATH", str(table))

    assert discovery._read_proc_arp() == {
        "192.168.1.20": "aa:bb:cc:dd:ee:ff",  # lowercased, because that is what WoL compares
        "192.168.1.21": "11:22:33:44:55:66",
    }


def test_read_proc_arp_skips_incomplete_and_zero_entries(tmp_path: Path, monkeypatch):
    # Flags 0x0 means the kernel asked but got no answer, and the placeholder MAC that
    # comes with it would be sent a magic packet that reaches nobody.
    table = _arp_file(
        tmp_path,
        "192.168.1.30     0x1         0x0         00:00:00:00:00:00     *        eth0\n"
        "192.168.1.31     0x1         0x2         00:00:00:00:00:00     *        eth0\n"
        "192.168.1.32     0x1         0x2         not-a-mac             *        eth0\n"
        "192.168.1.33     0x1         0x2         AA:BB:CC:DD:EE:FF     *        eth0\n",
    )
    monkeypatch.setattr(discovery, "_ARP_PATH", str(table))

    assert discovery._read_proc_arp() == {"192.168.1.33": "aa:bb:cc:dd:ee:ff"}


def test_read_proc_arp_is_empty_off_linux(tmp_path: Path, monkeypatch):
    # No /proc/net/arp (Windows, macOS, a stripped container): empty map, never an
    # exception, because detect_mac returning None is the supported "we don't know" answer.
    monkeypatch.setattr(discovery, "_ARP_PATH", str(tmp_path / "does-not-exist"))
    assert discovery._read_proc_arp() == {}


def test_detect_mac_uses_the_real_parser_by_default(tmp_path: Path, monkeypatch):
    # The wiring itself: detect_mac's default read_arp_table is _read_proc_arp, so a
    # correct parser and a correct lookup still add up to a MAC.
    table = _arp_file(
        tmp_path,
        "10.0.0.5         0x1         0x2         AA:BB:CC:DD:EE:FF     *        eth0\n",
    )
    monkeypatch.setattr(discovery, "_ARP_PATH", str(table))

    mac = detect_mac("pbs.local", prime=lambda _ip, _port: None, resolve=lambda _h: "10.0.0.5")

    assert mac == "aa:bb:cc:dd:ee:ff"
