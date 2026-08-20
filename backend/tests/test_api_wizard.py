"""Setup-wizard endpoints: auth guard, request wiring, error mapping, real keygen."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors.errors import ApiError, TokenExistsError
from app.connectors.provision import pbs_token_name
from app.core import wizard
from app.main import create_app


@pytest.fixture
def client(temp_config, temp_db):
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        yield c


def test_wizard_requires_auth(temp_config, temp_db):
    with TestClient(create_app()) as c:
        r = c.post("/api/wizard/pve/connect", json={"host": "pve.local"})
        assert r.status_code == 401


def test_pve_connect_passes_through(client, monkeypatch):
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return {"connected": True, "nodes": [], "storages": [], "token": None}

    monkeypatch.setattr(wizard, "pve_connect", fake_connect)
    r = client.post(
        "/api/wizard/pve/connect",
        json={"host": "pve.local", "mode": "root", "username": "root@pam", "password": "pw"},
    )
    assert r.status_code == 200
    assert r.json()["connected"] is True
    assert captured["host"] == "pve.local" and captured["mode"] == "root"
    assert captured["username"] == "root@pam"


def test_connector_error_maps_to_502(client, monkeypatch):
    def boom(**_kwargs):
        raise ApiError("connection refused")

    monkeypatch.setattr(wizard, "pve_connect", boom)
    r = client.post("/api/wizard/pve/connect", json={"host": "pve.local"})
    assert r.status_code == 502


def test_a_taken_token_name_is_409_not_502(client, monkeypatch):
    """Nothing failed upstream — we declined to replace a token the user has not agreed to
    lose. The wizard keys on the status to offer "replace it" instead of a connection error."""

    def taken(**_kwargs):
        raise TokenExistsError("An API token named 'joulenap' already exists for root@pam.")

    monkeypatch.setattr(wizard, "pbs_provision", taken)
    r = client.post(
        "/api/wizard/pbs/provision",
        json={"host": "pbs.local", "password": "pw", "datastore": "backup"},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_replace_token_is_forwarded(client, monkeypatch):
    captured = {}

    def fake_provision(**kwargs):
        captured.update(kwargs)
        return {"id": "root@pam!joulenap", "secret": "s"}

    monkeypatch.setattr(wizard, "pbs_provision", fake_provision)
    r = client.post(
        "/api/wizard/pbs/provision",
        json={
            "host": "pbs.local",
            "password": "pw",
            "datastore": "backup",
            "replace_token": True,
        },
    )
    assert r.status_code == 200
    assert captured["replace_token"] is True


def test_the_pbs_token_name_is_derived_from_the_datastore(client, monkeypatch):
    """Two devices on one backup server must not contend for a token name: naming both
    ``joulenap`` meant provisioning the second deleted the first one's token."""
    captured = {}

    def fake_provision(**kwargs):
        captured.update(kwargs)
        return {"id": "root@pam!joulenap-lab", "secret": "s"}

    monkeypatch.setattr(wizard, "pbs_provision", fake_provision)
    r = client.post(
        "/api/wizard/pbs/provision",
        json={"host": "pbs.local", "password": "pw", "datastore": "lab"},
    )
    assert r.status_code == 200
    assert captured["token_name"] == "joulenap-lab"


def test_an_explicit_token_name_still_wins(client, monkeypatch):
    """The field stays honoured for anyone driving the API directly."""
    captured = {}

    def fake_provision(**kwargs):
        captured.update(kwargs)
        return {"id": "root@pam!mine", "secret": "s"}

    monkeypatch.setattr(wizard, "pbs_provision", fake_provision)
    r = client.post(
        "/api/wizard/pbs/provision",
        json={
            "host": "pbs.local",
            "password": "pw",
            "datastore": "lab",
            "token_name": "mine",
        },
    )
    assert r.status_code == 200
    assert captured["token_name"] == "mine"


@pytest.mark.parametrize(
    ("datastore", "expected"),
    [
        ("lab", "joulenap-lab"),
        ("backup_2", "joulenap-backup_2"),
        ("dot.name", "joulenap-dot.name"),
        ("  spaced  ", "joulenap-spaced"),
        ("a b/c", "joulenap-a-b-c"),
        # Nothing survives sanitising, so fall back rather than send PBS a name it rejects.
        ("///", "joulenap"),
        ("", "joulenap"),
    ],
)
def test_token_name_sanitising(datastore, expected):
    assert pbs_token_name(datastore) == expected


def test_pbs_check_passes_through(client, monkeypatch):
    monkeypatch.setattr(
        wizard, "pbs_check", lambda **_k: {"reachable": True, "fingerprint": "AA:BB"}
    )
    r = client.post("/api/wizard/pbs/check", json={"host": "pbs.local"})
    assert r.json() == {"reachable": True, "fingerprint": "AA:BB"}


def test_pbs_grant_sync_passes_through(client, monkeypatch):
    captured = {}

    def fake_grant(**kwargs):
        captured.update(kwargs)
        return {"token_id": kwargs["token_id"], "roles": ["RemoteAdmin"]}

    monkeypatch.setattr(wizard, "pbs_grant_sync", fake_grant)
    r = client.post(
        "/api/wizard/pbs/grant-sync",
        json={
            "host": "pbs.local",
            "password": "pw",
            "api_token_id": "root@pam!joulenap",
            "fingerprint": "AA:BB",
        },
    )
    assert r.status_code == 200
    assert r.json()["roles"] == ["RemoteAdmin"]
    # the endpoint renames api_token_id -> token_id and defaults the root realm
    assert captured["token_id"] == "root@pam!joulenap"
    assert captured["username"] == "root@pam" and captured["password"] == "pw"
    assert "pw" not in r.text


def test_pbs_grant_sync_needs_a_password_and_a_token(client):
    r = client.post("/api/wizard/pbs/grant-sync", json={"host": "pbs.local", "password": "pw"})
    assert r.status_code == 422


def test_detect_mac_passes_through(client, monkeypatch):
    monkeypatch.setattr(wizard, "wol_detect_mac", lambda **_k: {"mac": "00:11:22:33:44:55"})
    r = client.post("/api/wizard/wol/detect-mac", json={"host": "pbs.local"})
    assert r.json()["mac"] == "00:11:22:33:44:55"


def test_ssh_keygen_generates_real_key(client):
    r = client.post("/api/wizard/ssh/keygen")
    assert r.status_code == 200
    body = r.json()
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["key_path"].endswith("id_ed25519")
    assert body["created"] is True
    # The restricted line to paste/install locks the key to poweroff only.
    assert body["authorized_keys_line"].startswith('command="systemctl poweroff",')
    assert body["authorized_keys_line"].endswith(body["public_key"])


def test_ssh_keygen_reuses_an_existing_key(client):
    """Adding a second PBS must not invalidate the first one's power-off: every device's
    ssh_key_path points at this same file, so a regenerated key would leave the first PBS
    trusting a public half no private key matches any more."""
    first = client.post("/api/wizard/ssh/keygen").json()
    key_file = Path(first["key_path"])
    written = key_file.read_bytes()

    second = client.post("/api/wizard/ssh/keygen").json()

    assert second["public_key"] == first["public_key"]
    assert second["created"] is False
    assert key_file.read_bytes() == written  # not rewritten, byte for byte


def test_ssh_keygen_replaces_an_unreadable_key(client):
    """A file that isn't a usable private key can't be what any PBS trusts either, so there
    is nothing to preserve — generate over it rather than failing the whole flow."""
    key_file = Path(client.post("/api/wizard/ssh/keygen").json()["key_path"])
    key_file.write_text("not a private key")

    body = client.post("/api/wizard/ssh/keygen").json()

    assert body["created"] is True
    assert body["public_key"].startswith("ssh-ed25519 ")


def test_ssh_install_passes_through(client, monkeypatch):
    captured = {}

    def fake_install(**kwargs):
        captured.update(kwargs)
        return {"installed": True}

    monkeypatch.setattr(wizard, "ssh_install", fake_install)
    r = client.post(
        "/api/wizard/ssh/install",
        json={"host": "pbs.local", "password": "pw", "public_key": "ssh-ed25519 AAAA"},
    )
    assert r.json() == {"installed": True}
    assert captured["host"] == "pbs.local" and captured["user"] == "root"


def test_wol_test_sends_a_packet_for_a_mac_not_yet_saved(client, monkeypatch):
    # The wizard tests a MAC it has just detected, before there is a device to hang it on —
    # so this takes the MAC in the body rather than reading one out of the config.
    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.core.wizard.send_magic_packet",
        lambda mac, broadcast=None, source_ip=None: sent.append((mac, broadcast)),
    )
    monkeypatch.setattr(
        "app.connectors.net.wol_target", lambda host, iface: ("192.0.2.255", "192.0.2.5")
    )

    resp = client.post(
        "/api/wizard/wol/test", json={"mac": "00:11:22:33:44:55", "host": "192.0.2.20"}
    )

    assert resp.status_code == 200 and resp.json()["sent"] is True
    assert sent == [("00:11:22:33:44:55", "192.0.2.255")]


def test_wol_test_falls_back_to_the_global_broadcast_without_a_host(client, monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.core.wizard.send_magic_packet",
        lambda mac, broadcast=None, source_ip=None: sent.append((mac, broadcast)),
    )

    resp = client.post("/api/wizard/wol/test", json={"mac": "00:11:22:33:44:55"})

    assert resp.status_code == 200
    assert sent == [("00:11:22:33:44:55", "255.255.255.255")]


# --- request-model defaults ---------------------------------------------------
#
# Every field below is a default the UI relies on by *omitting* it. Nothing pinned them,
# so the whole of api/wizard.py could be mutated freely without a single failure: a
# replace_token defaulting to True would have silently replaced a token on a live backup
# server, which is exactly what test_an_existing_token_is_never_replaced_silently guards
# one layer down.


def _capture(monkeypatch, name: str, result: dict) -> dict:
    """Patch a core-wizard entry point and hand back the kwargs the router forwarded."""
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(wizard, name, fake)
    return captured


def test_pve_connect_defaults_to_the_pve_port_no_tls_and_no_token_replacement(
    client, monkeypatch
):
    captured = _capture(
        monkeypatch, "pve_connect", {"connected": True, "nodes": [], "storages": [], "token": None}
    )

    assert client.post("/api/wizard/pve/connect", json={"host": "pve.local"}).status_code == 200

    assert captured["port"] == 8006  # PVE, not the PBS 8007 next door
    assert captured["verify_tls"] is False  # homelab certs are self-signed
    assert captured["replace_token"] is False  # never clobber a token unasked
    assert captured["mode"] == "token"  # the safe mode: no root password involved
    assert captured["token_name"] == "joulenap"


def test_pbs_provision_defaults_to_the_pbs_port_no_tls_and_no_token_replacement(
    client, monkeypatch
):
    captured = _capture(monkeypatch, "pbs_provision", {"id": "root@pam!joulenap", "secret": "s"})

    r = client.post(
        "/api/wizard/pbs/provision",
        json={"host": "pbs.local", "password": "pw", "datastore": "backup"},
    )
    assert r.status_code == 200

    assert captured["port"] == 8007
    assert captured["verify_tls"] is False
    assert captured["replace_token"] is False
    assert captured["username"] == "root@pam"
    assert captured["fingerprint"] == ""  # nothing pinned until the check step supplies one


def test_pbs_check_defaults_to_the_pbs_port(client, monkeypatch):
    captured = _capture(monkeypatch, "pbs_check", {"reachable": True, "fingerprint": None})

    assert client.post("/api/wizard/pbs/check", json={"host": "pbs.local"}).status_code == 200

    assert captured["port"] == 8007


def test_the_ssh_endpoints_default_to_port_22_and_the_root_user(client, monkeypatch):
    install = _capture(monkeypatch, "ssh_install", {"installed": True})
    client.post(
        "/api/wizard/ssh/install",
        json={"host": "pbs.local", "password": "pw", "public_key": "ssh-ed25519 AAAA"},
    )
    assert install["port"] == 22 and install["user"] == "root"

    hostkey = _capture(monkeypatch, "ssh_hostkey", {"key_type": "ssh-ed25519", "key_base64": "AA"})
    client.post("/api/wizard/ssh/hostkey", json={"host": "pbs.local"})
    assert hostkey["port"] == 22

    trust = _capture(monkeypatch, "ssh_trust", {"trusted": True})
    client.post(
        "/api/wizard/ssh/trust",
        json={"host": "pbs.local", "key_type": "ssh-ed25519", "key_base64": "AA"},
    )
    assert trust["port"] == 22


def test_storage_derive_and_grant_sync_default_to_no_tls_and_their_own_ports(
    client, monkeypatch
):
    # The two remaining wizard bodies, same contract as the ones above: a UI that omits
    # these fields must get the permissive-but-deliberate defaults, not whatever a later
    # edit leaves behind.
    derive = _capture(monkeypatch, "storage_derive", {"host": "pbs.local", "datastore": "backup"})
    r = client.post(
        "/api/wizard/storage/derive",
        json={
            "host": "pve.local",
            "api_token_id": "root@pam!joulenap",
            "api_token_secret": "sec",
            "storage_id": "pbs",
        },
    )
    assert r.status_code == 200
    assert derive["port"] == 8006 and derive["verify_tls"] is False

    grant = _capture(monkeypatch, "pbs_grant_sync", {"token_id": "t", "roles": ["RemoteAudit"]})
    r = client.post(
        "/api/wizard/pbs/grant-sync",
        json={"host": "pbs.local", "password": "pw", "api_token_id": "root@pam!joulenap"},
    )
    assert r.status_code == 200
    assert grant["port"] == 8007 and grant["verify_tls"] is False
    assert grant["username"] == "root@pam"
    assert grant["fingerprint"] == ""
