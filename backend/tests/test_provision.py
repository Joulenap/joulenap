"""PVE provisioner (ticket auth + token creation) and the PVE-side wizard helpers."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.connectors.errors import ApiError
from app.connectors.provision import (
    PBS_DATASTORE_ROLE,
    PBS_SYSTEM_ROLE,
    ROLE_ID,
    PbsProvisioner,
    PveProvisioner,
)
from app.core import wizard


def _json(payload):
    return httpx.Response(200, json={"data": payload})


def _pve_handler(captured: list, *, role_exists: bool = False):
    """A MockTransport handler covering ticket/roles/token/acl + version/nodes/storage."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        captured.append((request.method, path, dict(request.headers)))
        if path.endswith("/access/ticket"):
            return _json({"ticket": "PVE:tkt", "CSRFPreventionToken": "csrf123"})
        if path.endswith("/access/roles") and request.method == "POST":
            if role_exists:
                return httpx.Response(500, text="role already exists")
            return _json(None)
        if path.endswith(f"/access/roles/{ROLE_ID}") and request.method == "PUT":
            return _json(None)
        if "/token/" in path and request.method == "POST":
            return _json({"value": "the-secret", "full-tokenid": "root@pam!joulenap"})
        if path.endswith("/access/acl") and request.method == "PUT":
            return _json(None)
        if path.endswith("/version"):
            return _json({"version": "8.2.4"})
        if path.endswith("/nodes"):
            return _json([{"node": "pve", "status": "online"}])
        if path.endswith("/storage"):
            return _json(
                [{"storage": "pbs", "server": "10.0.0.5", "datastore": "backup",
                  "fingerprint": "AA:BB"}]
            )
        return httpx.Response(404)

    return handler


def _provisioner(handler) -> PveProvisioner:
    return PveProvisioner("pve.local", transport=httpx.MockTransport(handler))


def test_provision_token_runs_full_sequence():
    calls: list = []
    token = _provisioner(_pve_handler(calls)).provision_token("root@pam", "pw")

    assert token.token_id == "root@pam!joulenap"
    assert token.secret == "the-secret"
    methods_paths = [(m, p.split("/api2/json")[-1]) for m, p, _h in calls]
    assert ("POST", "/access/ticket") in methods_paths
    assert ("POST", "/access/roles") in methods_paths
    assert ("POST", "/access/users/root@pam/token/joulenap") in methods_paths
    assert ("PUT", "/access/acl") in methods_paths


def test_writes_carry_csrf_header_after_login():
    calls: list = []
    _provisioner(_pve_handler(calls)).provision_token("root@pam", "pw")
    # Every write after the ticket call must carry the CSRF token.
    writes = [h for m, p, h in calls if m in ("POST", "PUT") and not p.endswith("/ticket")]
    assert writes and all(h.get("csrfpreventiontoken") == "csrf123" for h in writes)


def test_ensure_role_updates_when_already_exists():
    calls: list = []
    # role_exists -> POST 500 should fall back to PUT (no exception raised).
    _provisioner(_pve_handler(calls, role_exists=True)).provision_token("root@pam", "pw")
    methods_paths = [(m, p.split("/api2/json")[-1]) for m, p, _h in calls]
    assert ("PUT", f"/access/roles/{ROLE_ID}") in methods_paths


def test_create_token_recreates_when_already_exists():
    calls: list = []
    deleted = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/access/ticket"):
            return _json({"ticket": "PVE:tkt", "CSRFPreventionToken": "csrf123"})
        if path.endswith("/access/roles") and request.method == "POST":
            return _json(None)
        if "/token/" in path and request.method == "POST":
            if not deleted["done"]:
                return httpx.Response(400, text="token already exists")
            return _json({"value": "fresh-secret", "full-tokenid": "root@pam!joulenap"})
        if "/token/" in path and request.method == "DELETE":
            deleted["done"] = True
            return _json(None)
        if path.endswith("/access/acl") and request.method == "PUT":
            return _json(None)
        return httpx.Response(404)

    token = _provisioner(handler).provision_token("root@pam", "pw")
    assert token.secret == "fresh-secret"
    methods = [(m, p.split("/api2/json")[-1]) for m, p in calls]
    assert ("DELETE", "/access/users/root@pam/token/joulenap") in methods


def test_login_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid credentials")

    with pytest.raises(ApiError):
        _provisioner(handler).login("root@pam", "wrong")


# --- PBS provisioner ---------------------------------------------------------


def _pbs_handler(captured: list):
    """MockTransport handler for PBS ticket/roles/token/acl provisioning."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        captured.append((request.method, path, body))
        if path.endswith("/access/ticket"):
            return _json({"ticket": "PBS:tkt", "CSRFPreventionToken": "csrf456"})
        if path.endswith("/access/roles") and request.method == "POST":
            return _json(None)
        if "/token/" in path and request.method == "POST":
            return _json({"value": "pbs-secret", "full-tokenid": "root@pam!joulenap"})
        if path.endswith("/access/acl") and request.method == "PUT":
            return _json(None)
        return httpx.Response(404)

    return handler


def test_pbs_provision_token_grants_datastore_scoped_acl():
    calls: list = []
    token = PbsProvisioner(
        "pbs.local", transport=httpx.MockTransport(_pbs_handler(calls))
    ).provision_token("root@pam", "pw", "backup")

    assert token.token_id == "root@pam!joulenap"
    assert token.secret == "pbs-secret"
    # PBS has no custom-role API, so we must NOT attempt to create one...
    assert not any(p.endswith("/access/roles") for _m, p, _d in calls)
    # ...and the token gets two built-in grants (PBS param names): DatastoreAdmin on the
    # datastore (GC/status) and Audit on /system (node load), both bound to the token.
    acls = {d["path"]: d for m, p, d in calls if m == "PUT" and p.endswith("/access/acl")}
    assert acls["/datastore/backup"]["role"] == PBS_DATASTORE_ROLE
    assert acls["/datastore/backup"]["auth-id"] == "root@pam!joulenap"
    assert acls["/system"]["role"] == PBS_SYSTEM_ROLE
    assert acls["/system"]["auth-id"] == "root@pam!joulenap"


def test_wizard_pbs_provision_defaults_realm_for_bare_username():
    calls: list = []
    result = wizard.pbs_provision(
        host="pbs.local",
        username="root",  # SSH-style, no realm
        password="pw",
        datastore="backup",
        transport=httpx.MockTransport(_pbs_handler(calls)),
    )
    assert result == {"id": "root@pam!joulenap", "secret": "pbs-secret"}
    # The bare "root" is qualified to "root@pam" for token creation.
    assert any("/access/users/root@pam/token/joulenap" in p for _m, p, _d in calls)


# --- core.wizard PVE helpers -------------------------------------------------


def test_wizard_pve_connect_token_mode():
    calls: list = []
    result = wizard.pve_connect(
        host="pve.local",
        mode="token",
        token_id="root@pam!joulenap",
        token_secret="s",
        transport=httpx.MockTransport(_pve_handler(calls)),
    )
    assert result["connected"] is True
    assert result["version"] == "8.2.4"
    assert result["nodes"] == [{"node": "pve", "status": "online"}]
    assert result["token"] is None
    # Storages come back enriched with the derived PBS fields.
    assert result["storages"][0]["host"] == "10.0.0.5"
    assert result["storages"][0]["datastore"] == "backup"
    assert result["storages"][0]["port"] == 8007


def test_wizard_pve_connect_root_mode_creates_token():
    result = wizard.pve_connect(
        host="pve.local",
        mode="root",
        username="root@pam",
        password="pw",
        transport=httpx.MockTransport(_pve_handler([])),
    )
    assert result["token"] == {"id": "root@pam!joulenap", "secret": "the-secret"}


def test_wizard_pve_connect_token_mode_requires_token():
    with pytest.raises(ApiError):
        wizard.pve_connect(host="pve.local", mode="token")


def test_wizard_storage_derive():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"server": "10.0.0.9", "datastore": "store", "fingerprint": "CC:DD"})

    pbs = wizard.storage_derive(
        host="pve.local",
        port=8006,
        verify_tls=False,
        token_id="root@pam!joulenap",
        token_secret="s",
        storage_id="pbs",
        transport=httpx.MockTransport(handler),
    )
    assert pbs == {"host": "10.0.0.9", "port": 8007, "datastore": "store", "fingerprint": "CC:DD"}
