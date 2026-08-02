"""The connector factories a route cycle builds its clients from."""

from app.config import PbsDevice
from app.jobs import deps


def test_connect_pbs_pins_when_fingerprint_present(monkeypatch):
    device = PbsDevice(id="pbs-01", host="pbs.local", fingerprint="AB:CD", managed_power=False)
    sentinel = object()
    captured = {}

    def fake_ctx(host, port, fp, **kw):
        captured.update(host=host, port=port, fp=fp)
        return sentinel

    monkeypatch.setattr(deps.tls, "pinned_ssl_context", fake_ctx)
    recorded = {}

    class FakePbs:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(deps, "PbsClient", FakePbs)
    deps._connect_pbs(device).close()
    assert captured["fp"] == "AB:CD"
    assert recorded["verify"] is sentinel  # the pinned context is actually wired to verify=


def test_connect_pbs_no_fingerprint_leaves_verify_false(monkeypatch):
    device = PbsDevice(id="pbs-01", host="pbs.local", managed_power=False)  # fingerprint empty

    def boom(*a, **k):
        raise AssertionError("should not pin without a fingerprint")

    monkeypatch.setattr(deps.tls, "pinned_ssl_context", boom)
    deps._connect_pbs(device).close()  # must not raise
