import ssl

from app.config import Config
from app.jobs import deps


def test_build_pbs_pins_when_fingerprint_present(monkeypatch):
    cfg = Config()
    cfg.pbs.host = "pbs.local"
    cfg.pbs.fingerprint = "AB:CD"
    captured = {}

    def fake_ctx(host, port, fp, **kw):
        captured.update(host=host, port=port, fp=fp)
        # Return a real SSLContext for httpx to use
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    monkeypatch.setattr(deps.tls, "pinned_ssl_context", fake_ctx)
    client = deps._build_pbs(cfg)
    assert captured["fp"] == "AB:CD"
    client.close()


def test_build_pbs_no_fingerprint_leaves_verify_false(monkeypatch):
    cfg = Config()
    cfg.pbs.host = "pbs.local"  # fingerprint empty
    def boom(*a, **k):
        raise AssertionError("should not pin without a fingerprint")
    monkeypatch.setattr(deps.tls, "pinned_ssl_context", boom)
    deps._build_pbs(cfg).close()  # must not raise
