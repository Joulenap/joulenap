"""core.wizard: fingerprint-pinned PBS provisioning (JN-002)."""

from __future__ import annotations

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
