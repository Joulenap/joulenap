"""GET /api/update — opt-in, cached GitHub release check."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.api import update
from app.main import create_app


@pytest.fixture
def client(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr(update, "_cache", None)
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        yield c


def _enable(client):
    body = client.get("/api/config").json()
    body["app"]["update_check"] = True
    assert client.put("/api/config", json=body).status_code == 200


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v0.5.0", "0.4.4", True),
        ("v0.4.4", "0.4.4", False),
        ("v0.4.3", "0.4.4", False),
        ("v1.0.0", "0.9.9", True),
        ("0.10.0", "0.9.0", True),  # numeric, not lexicographic
        ("v0.5.0-beta", "0.5.0", False),  # suffix ignored: same release
        ("garbage", "0.4.4", False),
    ],
)
def test_version_compare(latest, current, expected):
    assert (update._parse(latest) > update._parse(current)) is expected


def test_disabled_by_default_never_calls_out(client, monkeypatch):
    monkeypatch.setattr(
        update, "_fetch_latest", lambda: pytest.fail("no network when update_check is off")
    )
    body = client.get("/api/update").json()
    assert body == {
        "current": __version__,
        "latest": "",
        "update_available": False,
        "url": update._RELEASES_PAGE,
    }


def test_reports_and_caches_a_newer_release(client, monkeypatch):
    calls = []

    def fake():
        calls.append(1)
        return "v99.0.0"

    monkeypatch.setattr(update, "_fetch_latest", fake)
    _enable(client)

    body = client.get("/api/update").json()
    assert body["update_available"] is True
    assert body["latest"] == "v99.0.0"
    assert body["current"] == __version__

    client.get("/api/update")
    assert len(calls) == 1  # second call served from the cache


def test_fetch_failure_is_silent(client, monkeypatch):
    monkeypatch.setattr(update, "_fetch_latest", lambda: "")
    _enable(client)
    body = client.get("/api/update").json()
    assert body["latest"] == ""
    assert body["update_available"] is False


def test_fetch_latest_swallows_transport_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(update.httpx, "get", boom)
    assert update._fetch_latest() == ""


def test_requires_auth(temp_config, temp_db):
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/update").status_code == 401
