"""Setup-wizard endpoints: auth guard, request wiring, error mapping, real keygen."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.connectors.errors import ApiError
from app.core import wizard
from app.main import create_app


@pytest.fixture
def client(temp_config, temp_db):
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "secret"})
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
    # The restricted line to paste/install locks the key to poweroff only.
    assert body["authorized_keys_line"].startswith('command="systemctl poweroff",')
    assert body["authorized_keys_line"].endswith(body["public_key"])


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


def test_reset_clears_connection_but_keeps_tuning(client):
    before = client.get("/api/config").json()
    # The example config ships a configured PVE/PBS, so we start from a set-up state.
    assert before["pve"]["host"] and before["pbs"]["host"]
    schedule = before["backup"]["schedule"]

    assert client.post("/api/wizard/reset").json() == {"ok": True}

    after = client.get("/api/config").json()
    # Connection identity is wiped...
    assert after["pve"]["host"] == "" and after["pve"]["api_token_id"] == ""
    assert after["pbs"]["host"] == "" and after["pbs"]["mac"] == ""
    assert after["pbs"]["datastore"] == "" and after["pbs"]["fingerprint"] == ""
    # ...but tuning the wizard didn't own is preserved.
    assert after["backup"]["schedule"] == schedule
    assert after["pve"]["port"] == before["pve"]["port"]
