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
