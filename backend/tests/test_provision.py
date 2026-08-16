"""PVE provisioner (ticket auth + token creation) and the PVE-side wizard helpers."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.connectors.errors import ApiError, TokenExistsError
from app.connectors.provision import (
    PBS_DATASTORE_ROLE,
    PBS_REMOTE_ROLES,
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


def _exists_handler(calls: list, *, token_present: bool = True):
    """Ticket/roles/acl plus a token endpoint whose POST always says "already exists".

    ``token_present`` drives the GET the provisioner uses to confirm that diagnosis before
    it deletes anything.
    """
    deleted = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/access/ticket"):
            return _json({"ticket": "PVE:tkt", "CSRFPreventionToken": "csrf123"})
        if path.endswith("/access/roles") and request.method == "POST":
            return _json(None)
        if "/token/" in path and request.method == "GET":
            if token_present:
                return _json({"expire": 0, "privsep": 1})
            return httpx.Response(400, text="no such token 'joulenap' for user 'root@pam'")
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

    return handler


def test_an_existing_token_is_replaced_only_when_asked():
    calls: list = []
    token = _provisioner(_exists_handler(calls)).provision_token(
        "root@pam", "pw", replace_existing=True
    )
    assert token.secret == "fresh-secret"
    methods = [(m, p.split("/api2/json")[-1]) for m, p in calls]
    assert ("DELETE", "/access/users/root@pam/token/joulenap") in methods


def test_an_existing_token_is_never_replaced_silently():
    """The secret is only shown at creation, so "reuse it" is impossible and replacing it
    breaks every other consumer — most obviously the PBS storage entry on a Proxmox host,
    which the wizard itself tells the user to create. It has to be asked for."""
    calls: list = []
    with pytest.raises(TokenExistsError, match="already exists"):
        _provisioner(_exists_handler(calls)).provision_token("root@pam", "pw")

    methods = [m for m, _p in calls]
    assert "DELETE" not in methods


def test_a_400_that_is_not_a_duplicate_never_deletes_the_token():
    """The create's 400/500 is only a hint (``ensure_role`` uses the same pair), so a 400 for
    any other reason must surface as itself rather than taking a live token down with it."""
    calls: list = []
    with pytest.raises(ApiError) as exc_info:
        _provisioner(_exists_handler(calls, token_present=False)).provision_token("root@pam", "pw")

    assert exc_info.value.status == 400
    assert "DELETE" not in [m for m, _p in calls]


def test_create_token_reraises_original_when_delete_fails():
    """If the token exists (create 400, GET confirms) and the cleanup DELETE also fails,
    create_token must re-raise the ORIGINAL create error, not the delete's."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/access/ticket"):
            return _json({"ticket": "PVE:tkt", "CSRFPreventionToken": "csrf123"})
        if path.endswith("/access/roles") and request.method == "POST":
            return _json(None)
        if "/token/" in path and request.method == "GET":
            return _json({"expire": 0, "privsep": 1})
        if "/token/" in path and request.method == "POST":
            return httpx.Response(400, text="token already exists")
        if "/token/" in path and request.method == "DELETE":
            return httpx.Response(500, text="delete forbidden")
        if path.endswith("/access/acl") and request.method == "PUT":
            return _json(None)
        return httpx.Response(404)

    with pytest.raises(ApiError) as exc_info:
        _provisioner(handler).provision_token("root@pam", "pw", replace_existing=True)
    # The propagated error is the original create failure (400), not the delete's (500).
    assert exc_info.value.status == 400


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
    # ...and the token gets built-in grants (PBS param names): DatastoreAdmin on the
    # datastore (GC/status) and Audit on /system (node load), both bound to the token.
    acls = _acls(calls)
    assert ("/datastore/backup", PBS_DATASTORE_ROLE) in acls
    assert ("/system", PBS_SYSTEM_ROLE) in acls
    assert all(
        d["auth-id"] == "root@pam!joulenap"
        for m, p, d in calls
        if m == "PUT" and p.endswith("/access/acl")
    )


def test_pbs_provision_token_grants_both_remote_roles_for_sync():
    """Sync routes have Joulenap create the remote entry and the sync job itself, and PBS
    refuses ACL writes from a token — so if these two grants don't happen here, while the
    root ticket is still held, they can never happen over the API at all. RemoteAdmin alone
    covers pull; push additionally needs Remote.DatastoreBackup, and push + remove-vanished
    Remote.DatastorePrune (both in RemoteDatastoreAdmin)."""
    calls: list = []
    PbsProvisioner(
        "pbs.local", transport=httpx.MockTransport(_pbs_handler(calls))
    ).provision_token("root@pam", "pw", "backup")

    acls = _acls(calls)
    for role in PBS_REMOTE_ROLES:
        assert ("/remote", role) in acls


def _acls(calls: list) -> list[tuple[str, str]]:
    """The (path, role) pairs granted, in order. A list, not a dict: /remote takes two roles
    and keying by path would silently drop one."""
    return [
        (d["path"], d["role"]) for m, p, d in calls if m == "PUT" and p.endswith("/access/acl")
    ]


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
