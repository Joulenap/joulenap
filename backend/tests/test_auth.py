"""Auth flow: first-run setup, login/logout, session-guarded routes, redaction."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.main import create_app


@pytest.fixture
def client(temp_config, temp_db):
    # temp_config routes JOULENAP_CONFIG/DATA_DIR at a tmp path; create_app loads it.
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_secret_key_is_generated(temp_config, temp_db):
    # Example ships secret_key: CHANGE_ME; startup should replace + persist it.
    create_app()
    cfg = load_config(temp_config)
    assert cfg.app.secret_key not in ("", "CHANGE_ME")
    assert len(cfg.app.secret_key) >= 32


def test_status_reports_setup_needed(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["setup_needed"] is True
    assert body["authenticated"] is False


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_setup_then_authenticated(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
    assert r.status_code == 201
    assert r.json() == {"username": "admin"}
    # Session established by setup.
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "admin"
    # Status now reflects configured + authenticated.
    st = client.get("/api/auth/status").json()
    assert st["setup_needed"] is False and st["authenticated"] is True


def test_setup_rejected_when_account_exists(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
    r = client.post("/api/auth/setup", json={"username": "other", "password": "yyyyyyyy"})
    assert r.status_code == 409


def test_setup_persists_timezone(client):
    # The first-run screen sends the browser-detected timezone; it must land in app.timezone.
    r = client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "secret12", "timezone": "America/New_York"},
    )
    assert r.status_code == 201
    cfg = client.get("/api/config").json()
    assert cfg["app"]["timezone"] == "America/New_York"


def test_setup_without_timezone_keeps_default(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
    assert r.status_code == 201
    cfg = client.get("/api/config").json()
    assert cfg["app"]["timezone"] == ""


def test_setup_validation(client):
    short_user = client.post("/api/auth/setup", json={"username": "ab", "password": "secret12"})
    assert short_user.status_code == 422
    short_pass = client.post("/api/auth/setup", json={"username": "abc", "password": "no"})
    assert short_pass.status_code == 422


def test_login_logout_cycle(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
    client.post("/api/logout")
    assert client.get("/api/auth/me").status_code == 401

    bad = client.post("/api/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401

    ok = client.post("/api/login", json={"username": "admin", "password": "secret12"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "admin"


def test_password_is_hashed_not_plaintext(client, temp_config):
    client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
    cfg = load_config(temp_config)
    assert cfg.app.auth.password_hash.startswith("$2")
    assert "secret12" not in cfg.app.auth.password_hash


def test_setup_rejects_short_password(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "1234"})
    assert r.status_code == 422
