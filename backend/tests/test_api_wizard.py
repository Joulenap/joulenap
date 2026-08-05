"""Setup-wizard endpoints: auth guard, request wiring, error mapping, real keygen."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors.errors import ApiError
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
