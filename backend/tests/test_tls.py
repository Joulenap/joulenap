import ssl
from unittest import mock

import pytest

from app.connectors import tls
from app.connectors.errors import ApiError


# A tiny self-signed DER fixture is generated once via cryptography (already a dep).
def _self_signed_der() -> bytes:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.x509.oid import NameOID

    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pbs.local")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(key, None)
    )
    from cryptography.hazmat.primitives.serialization import Encoding

    return cert.public_bytes(Encoding.DER)


def test_fingerprint_hex_matches_normalized_input():
    der = _self_signed_der()
    fp = tls.fingerprint_hex(der)
    assert fp == fp.upper() and ":" in fp
    assert tls.normalize_fingerprint(f"sha256:{fp.lower()}") == fp


def test_pinned_context_builds_on_match():
    der = _self_signed_der()
    fp = tls.fingerprint_hex(der)
    ctx = tls.pinned_ssl_context("pbs", 8007, fp, fetch_der=lambda h, p: der)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_pinned_context_raises_on_mismatch():
    der = _self_signed_der()
    with pytest.raises(ApiError, match="fingerprint changed"):
        tls.pinned_ssl_context("pbs", 8007, "AA:BB:CC", fetch_der=lambda h, p: der)


def test_fetch_peer_der_speaks_before_it_leaves(monkeypatch):
    """A connection that only shakes hands and vanishes makes PBS log a failed poll (#44),
    so the probe asks for the login page and reads the answer out before closing."""
    tls_sock = mock.MagicMock()
    tls_sock.getpeercert.return_value = b"DER"
    tls_sock.recv.side_effect = [b"HTTP/1.0 200 OK", b""]
    ctx = mock.MagicMock(**{"wrap_socket.return_value": tls_sock})
    monkeypatch.setattr(tls.ssl, "create_default_context", lambda *a, **k: ctx)
    monkeypatch.setattr(tls.socket, "create_connection", mock.MagicMock())

    assert tls.fetch_peer_der("pbs", 8007) == b"DER"
    assert tls_sock.sendall.call_args.args[0].startswith(b"GET / HTTP/1.0")
    tls_sock.close.assert_called_once_with()

    # A server that won't talk must not fail the read, nor leak the socket.
    tls_sock.reset_mock()
    tls_sock.sendall.side_effect = OSError("peer gone")
    assert tls.fetch_peer_der("pbs", 8007) == b"DER"
    tls_sock.close.assert_called_once_with()
