"""core.wizard: fingerprint-pinned PBS provisioning (JN-002)."""

from __future__ import annotations

import pytest

from app.connectors.errors import ApiError
from app.core import wizard as wiz


def test_pbs_provision_pins_when_fingerprint_given(monkeypatch):
    seen = {}

    class FakeProv:
        def __init__(self, host, port, verify, transport=None):
            seen["verify"] = verify

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def provision_token(self, *a, **k):
            class T:
                token_id, secret = "id", "sec"

            return T()

    monkeypatch.setattr(wiz, "PbsProvisioner", FakeProv)
    monkeypatch.setattr(wiz.tls, "pinned_ssl_context", lambda *a, **k: "CTX")
    wiz.pbs_provision(host="h", username="root", password="p", datastore="d", fingerprint="AB:CD")
    assert seen["verify"] == "CTX"


def test_pbs_grant_sync_adds_both_remote_roles_to_the_existing_token(monkeypatch):
    """The re-provision path: a root login, two ACL writes, and no new token."""
    calls = []

    class FakeProv:
        def __init__(self, host, port, verify, transport=None):
            calls.append(("init", verify))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, userid, password):
            calls.append(("login", userid, password))

        def grant_acl(self, token_id, path, role):
            calls.append(("acl", token_id, path, role))

        def create_token(self, *a, **k):  # pragma: no cover - must never be reached
            raise AssertionError("grant-sync must not mint a new token")

    monkeypatch.setattr(wiz, "PbsProvisioner", FakeProv)
    monkeypatch.setattr(wiz.tls, "pinned_ssl_context", lambda *a, **k: "CTX")
    out = wiz.pbs_grant_sync(
        host="pbs",
        username="root",
        password="s3cr3t",
        token_id="root@pam!joulenap",
        fingerprint="AB",
    )

    assert calls[0] == ("init", "CTX")  # fingerprint pinned, like pbs_provision
    assert calls[1] == ("login", "root@pam", "s3cr3t")  # realm-less username gets @pam
    assert [c[1:] for c in calls[2:]] == [
        ("root@pam!joulenap", "/remote", role) for role in wiz.PBS_REMOTE_ROLES
    ]
    assert out["roles"] == list(wiz.PBS_REMOTE_ROLES)
    assert "s3cr3t" not in repr(out)  # the password never rides back out


def test_ssh_hostkey_and_trust(monkeypatch, tmp_path):
    from app.core import wizard as wiz

    monkeypatch.setattr(
        wiz.ssh, "scan_host_key", lambda h, p=22: ("ssh-ed25519", "AAAA", "SHA256:xx")
    )
    saved = {}
    monkeypatch.setattr(
        wiz.ssh,
        "save_host_key",
        lambda host, kt, kb, port=22: saved.update(host=host, kt=kt),
    )
    assert wiz.ssh_hostkey(host="pbs")["fingerprint"] == "SHA256:xx"
    assert wiz.ssh_trust(host="pbs", key_type="ssh-ed25519", key_base64="AAAA")["trusted"] is True
    assert saved["host"] == "pbs"


def test_root_credentials_are_refused_over_an_unverified_connection():
    """Without a fingerprint and without verify_tls, nothing has authenticated the box the
    root password is about to be sent to. Fail instead of shipping it."""
    for fn, extra in (
        (wiz.pbs_provision, {"datastore": "backup"}),
        (wiz.pbs_grant_sync, {"token_id": "root@pam!joulenap"}),
    ):
        with pytest.raises(ApiError, match="Refusing to send root credentials"):
            fn(host="pbs", username="root", password="s3cr3t", fingerprint="", **extra)


def test_root_credentials_are_allowed_when_tls_is_verified_without_a_pin(monkeypatch):
    """verify_tls is the deliberate opt-out: a PBS with a certificate a CA vouches for."""
    seen = {}

    class FakeProv:
        def __init__(self, host, port, verify, transport=None):
            seen["verify"] = verify

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def grant_acl(self, *a):
            pass

    monkeypatch.setattr(wiz, "PbsProvisioner", FakeProv)
    wiz.pbs_grant_sync(
        host="pbs", username="root", password="s3cr3t", token_id="t", verify_tls=True
    )
    assert seen["verify"] is True
