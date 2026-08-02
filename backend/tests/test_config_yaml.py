"""GET/PUT /api/config/yaml — the Advanced tab's raw config editor."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import REDACTED, load_config
from app.main import create_app


@pytest.fixture
def client(temp_config, temp_db):
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        yield c


def _text(client) -> str:
    resp = client.get("/api/config/yaml")
    assert resp.status_code == 200
    return resp.json()["yaml"]


def _put(client, text: str):
    return client.put("/api/config/yaml", json={"yaml": text})


def test_get_returns_the_redacted_config_as_yaml(client):
    doc = yaml.safe_load(_text(client))
    assert doc["pves"][0]["host"] == "192.0.2.10"
    assert doc["pves"][0]["api_token_secret"] == REDACTED  # never ships the real secret
    assert doc["app"]["auth"]["password_hash"] == REDACTED


def test_round_trip_changes_nothing(client, temp_config):
    before = load_config(temp_config).model_dump()
    assert _put(client, _text(client)).status_code == 200
    assert load_config(temp_config).model_dump() == before


def test_edit_applies_and_secrets_survive(client, temp_config):
    text = _text(client).replace("bwlimit: 0", "bwlimit: 5000")
    assert _put(client, text).status_code == 200

    saved = load_config(temp_config)
    assert saved.routes[0].options.bwlimit == 5000
    assert saved.pves[0].api_token_secret == "test-pve-secret"  # restored from the sentinel


def test_omitted_keys_keep_their_stored_value(client, temp_config):
    # Deep-merge semantics: deleting a section must not wipe the token behind it.
    assert _put(client, "maintenance:\n  history:\n    retention_days: 42\n").status_code == 200
    saved = load_config(temp_config)
    assert saved.maintenance.history.retention_days == 42
    assert saved.pves[0].api_token_secret == "test-pve-secret"
    assert saved.pves[0].host == "192.0.2.10"


def test_malformed_yaml_reports_a_line(client):
    resp = _put(client, "app:\n  language: en\n bad indent: x\n")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["line"] == 3
    assert detail["message"]


def test_non_mapping_document_is_rejected(client):
    resp = _put(client, "- just\n- a list\n")
    assert resp.status_code == 422
    assert "mapping" in resp.json()["detail"]["message"]


def test_unknown_key_is_rejected_with_its_path(client):
    resp = _put(client, "maintenance:\n  history:\n    retention_dayz: 10\n")
    assert resp.status_code == 422
    assert "maintenance.history.retention_dayz" in resp.json()["detail"]["message"]


def test_a_deleted_0_9_section_is_rejected_rather_than_silently_ignored(client):
    # The loader strips pve:/pbs:/backup: from an old file on disk so the app still boots.
    # A human *typing* one into the editor is a different thing: it must be told the section
    # is gone, not have its edit accepted and dropped.
    resp = _put(client, "backup:\n  bwlimit: 10\n")
    assert resp.status_code == 422
    assert "backup" in resp.json()["detail"]["message"]


def test_invalid_cron_is_rejected_through_the_yaml_path(client):
    # Proves the shared guards (BE-B1) still run for the editor. A list value replaces
    # wholesale, so the route has to be sent in full.
    doc = yaml.safe_load(_text(client))
    doc["routes"][0]["schedule"]["cron"] = "0 4 * *"  # 4 fields, unparseable
    resp = _put(client, yaml.safe_dump({"routes": doc["routes"]}))
    assert resp.status_code == 422
    assert "schedule.cron" in resp.json()["detail"]["message"]


def test_a_bad_mac_is_rejected_through_the_yaml_path(client):
    doc = yaml.safe_load(_text(client))
    doc["pbss"][0]["mac"] = "not-a-mac"
    resp = _put(client, yaml.safe_dump({"pbss": doc["pbss"]}))
    assert resp.status_code == 422
    assert "mac" in resp.json()["detail"]["message"]


def test_requires_auth(temp_config, temp_db):
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/config/yaml").status_code == 401
        assert c.put("/api/config/yaml", json={"yaml": "app: {}"}).status_code == 401
