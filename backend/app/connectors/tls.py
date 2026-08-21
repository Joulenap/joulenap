"""TLS fingerprint pinning for the PBS API client.

PBS certs are typically self-signed; PVE stores the PBS cert's SHA-256 fingerprint in its
storage config (a trusted channel), which the wizard captures. This module turns that
fingerprint into an ``ssl.SSLContext`` that trusts *only* the matching cert, so an on-LAN
MITM can't present its own cert. No new dependency — stdlib ``ssl`` + ``hashlib``.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
from collections.abc import Callable

from .errors import ApiError


def fetch_peer_der(host: str, port: int, timeout: float = 5.0) -> bytes:
    """Return the DER-encoded certificate the server at ``host:port`` presents.

    Connects without verifying (the cert is usually self-signed); the caller decides
    whether to trust it. Raises :class:`ApiError` if no cert can be read.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            tls_sock = context.wrap_socket(sock, server_hostname=host)
            try:
                der = tls_sock.getpeercert(binary_form=True)
                _say_hello(tls_sock, host)
            finally:
                tls_sock.close()
    except OSError as exc:
        raise ApiError(f"Could not read TLS certificate from {host}:{port}: {exc}") from exc
    if not der:
        raise ApiError(f"No TLS certificate presented by {host}:{port}")
    return der


def _say_hello(tls_sock: ssl.SSLSocket, host: str) -> None:
    """Ask for the login page and wait for the answer, so the server sees a visitor.

    proxmox-backup-proxy builds the API service for a connection, peer address and all, in
    the moment right after the handshake. A client that hangs up in that same moment makes
    it log a failed poll, once per dashboard refresh (#44). One round trip is enough to be
    somebody. ``GET /`` needs no credentials, while ``/api2/json/version`` would answer 401
    and log two lines of its own.
    """
    try:
        tls_sock.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        while tls_sock.recv(8192):  # read the answer out, or the close resets the connection
            pass
    except OSError:
        pass  # the certificate is what we came for, and the handshake already proved it is up


def fingerprint_hex(der: bytes) -> str:
    """SHA-256 of a DER cert as uppercase colon-separated hex (PBS's displayed form)."""
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def normalize_fingerprint(value: str) -> str:
    """Canonicalise a fingerprint for comparison: strip an optional ``sha256:`` prefix and
    whitespace, upper-case, and re-group into colon-separated byte pairs."""
    v = value.strip()
    if ":" in v and v.lower().startswith("sha256:"):
        v = v.split(":", 1)[1]
    hexits = v.replace(":", "").replace(" ", "").upper()
    return ":".join(hexits[i : i + 2] for i in range(0, len(hexits), 2))


def _der_to_pem(der: bytes) -> str:
    return ssl.DER_cert_to_PEM_cert(der)


def pinned_ssl_context(
    host: str,
    port: int,
    fingerprint: str,
    *,
    fetch_der: Callable[[str, int], bytes] = fetch_peer_der,
) -> ssl.SSLContext:
    """Build an SSLContext that trusts only the cert matching ``fingerprint``.

    Fetches the presented cert, compares its SHA-256 to the pinned value (mismatch raises
    :class:`ApiError`), then trusts exactly that cert (``cadata`` + ``PARTIAL_CHAIN`` so a
    non-CA leaf works as its own anchor — covers self-signed and CA-signed PBS certs).
    """
    der = fetch_der(host, port)
    if normalize_fingerprint(fingerprint_hex(der)) != normalize_fingerprint(fingerprint):
        raise ApiError(
            f"PBS TLS fingerprint changed: expected {normalize_fingerprint(fingerprint)}, "
            f"got {fingerprint_hex(der)} — re-run PBS detection if the cert was renewed"
        )
    ctx = ssl.create_default_context(cadata=_der_to_pem(der))
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return ctx
