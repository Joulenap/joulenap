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
            with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                der = tls_sock.getpeercert(binary_form=True)
    except OSError as exc:
        raise ApiError(f"Could not read TLS certificate from {host}:{port}: {exc}") from exc
    if not der:
        raise ApiError(f"No TLS certificate presented by {host}:{port}")
    return der


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
