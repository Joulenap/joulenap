from app.config import Config
from app.jobs import deps


def test_build_pbs_pins_when_fingerprint_present(monkeypatch):
    cfg = Config()
    cfg.pbs.host = "pbs.local"
    cfg.pbs.fingerprint = "AB:CD"
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
    deps._build_pbs(cfg).close()
    assert captured["fp"] == "AB:CD"
    assert recorded["verify"] is sentinel  # the pinned context is actually wired to verify=


def test_build_pbs_no_fingerprint_leaves_verify_false(monkeypatch):
    cfg = Config()
    cfg.pbs.host = "pbs.local"  # fingerprint empty
    def boom(*a, **k):
        raise AssertionError("should not pin without a fingerprint")
    monkeypatch.setattr(deps.tls, "pinned_ssl_context", boom)
    deps._build_pbs(cfg).close()  # must not raise
