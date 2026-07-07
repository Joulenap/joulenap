import base64

import paramiko

from app.connectors import ssh


def _make_key_b64() -> tuple[str, str]:
    # paramiko 5.x has no Ed25519Key.generate(); RSAKey.generate() is the
    # portable way to mint a throwaway key for these round-trip tests.
    key = paramiko.RSAKey.generate(2048)
    return key.get_name(), key.get_base64()


def test_save_then_known_roundtrip(tmp_path):
    kt, kb = _make_key_b64()
    path = tmp_path / "known_hosts"
    assert ssh.host_key_known("pbs.local", path=path) is False
    ssh.save_host_key("pbs.local", kt, kb, path=path)
    assert ssh.host_key_known("pbs.local", path=path) is True


def test_strict_client_rejects_unknown_key(tmp_path):
    client = ssh.strict_client(path=tmp_path / "known_hosts")
    assert isinstance(client._policy, paramiko.RejectPolicy)
    client.close()


def test_scan_host_key_returns_fingerprint():
    kt, kb = _make_key_b64()
    key = (
        paramiko.PKey.from_type_string(kt, base64.b64decode(kb))
        if hasattr(paramiko.PKey, "from_type_string")
        else paramiko.RSAKey(data=base64.b64decode(kb))
    )

    class FakeTransport:
        def __init__(self, sock):
            pass

        def start_client(self, timeout=None):
            pass

        def get_remote_server_key(self):
            return key

        def close(self):
            pass

    ktype, kb64, fp = ssh.scan_host_key(
        "pbs.local", connect=lambda h, p, t: FakeTransport(None)
    )
    assert ktype == kt and kb64 == kb and fp.startswith("SHA256:")
