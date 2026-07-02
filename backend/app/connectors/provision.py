"""PVE/PBS auto-provisioning for the setup wizard (root-based "quick setup").

Given root credentials once, the app creates a minimal-privilege role + API token for
itself — on **PVE** (to list guests and run vzdump) and on **PBS** (to read datastore
status and start GC) — so the user never pastes a token and the password is discarded
right after. Both flows use ticket auth (cookie + CSRF header), which the token-only API
clients don't cover, so the provisioning clients live here. PVE and PBS speak the same
Proxmox API shape; only the auth cookie name, the ACL parameter names and the role
privileges differ, so the shared logic sits in a base class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ApiError

# PVE role: least privilege for the backup cycle. Datastore.Allocate (not just
# AllocateSpace) is required because vzdump with prune-backups (retention) deletes old
# backups on the target storage — without it PVE rejects the vzdump with a 403.
ROLE_ID = "Joulenap"
ROLE_PRIVS = "VM.Audit,VM.Backup,Datastore.Audit,Datastore.AllocateSpace,Datastore.Allocate"

# PBS roles for the token. Unlike PVE, PBS has no API to create custom roles
# (POST /access/roles 404s), so we grant built-ins scoped by path:
#   - DatastoreAdmin on the datastore: GC (Datastore.Modify) + status (Datastore.Audit).
#   - Audit on /system: read-only node status (CPU / RAM / network for the dashboard).
PBS_DATASTORE_ROLE = "DatastoreAdmin"
PBS_SYSTEM_ROLE = "Audit"

_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE"})


@dataclass
class CreatedToken:
    token_id: str  # full id, e.g. "root@pam!joulenap"
    secret: str  # shown by PVE/PBS only once, at creation


class _Provisioner:
    """Shared ticket-authenticated client for the one-time provisioning steps."""

    # Subclasses set the auth cookie name their API issues.
    _cookie_name: str = ""

    def __init__(
        self,
        host: str,
        port: int,
        verify: bool = False,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=f"https://{host}:{port}/api2/json",
            verify=verify,
            timeout=timeout,
            transport=transport,
        )
        self._csrf: str | None = None

    # --- auth ----------------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """Exchange username/password for a ticket; arms the cookie + CSRF token."""
        data = self._request(
            "POST", "/access/ticket", data={"username": username, "password": password}
        )
        ticket = data.get("ticket")
        self._csrf = data.get("CSRFPreventionToken")
        if not ticket or not self._csrf:
            raise ApiError("Login did not return a ticket")
        self._client.cookies.set(self._cookie_name, ticket)

    # --- provisioning steps --------------------------------------------------

    def ensure_role(self, role_id: str, privs: str) -> None:
        """Create the role, or update its privileges if it already exists."""
        try:
            self._request("POST", "/access/roles", data={"roleid": role_id, "privs": privs})
        except ApiError as exc:
            # Already exists -> bring its privileges up to date instead of failing.
            if exc.status in (400, 500):
                self._request("PUT", f"/access/roles/{role_id}", data={"privs": privs})
            else:
                raise

    # Extra params sent when creating a token: PVE wants ``privsep``; PBS has no privsep
    # concept and rejects unknown params, so it leaves this empty.
    _token_create_params: dict[str, Any] = {}

    def create_token(self, userid: str, token_name: str) -> CreatedToken:
        """Create an API token for ``userid``; returns its full id + one-time secret.

        Idempotent for quick setup: if a token of this name already exists (the server
        rejects the create with 400/500), delete it and recreate so we get a usable secret
        — the secret is only revealed at creation time, so reusing the old token isn't
        possible. If the delete then fails the token never existed, so the create failed
        for some other reason; re-raise that original error rather than the delete's.
        """
        path = f"/access/users/{userid}/token/{token_name}"
        payload = self._token_create_params or None
        try:
            data = self._request("POST", path, data=payload)
        except ApiError as exc:
            if exc.status not in (400, 500):
                raise
            try:
                self._request("DELETE", path)
            except ApiError:
                raise exc from None
            data = self._request("POST", path, data=payload)
        secret = data.get("value")
        full_id = data.get("full-tokenid") or f"{userid}!{token_name}"
        if not secret:
            raise ApiError("Server did not return a token secret")
        return CreatedToken(token_id=full_id, secret=secret)

    def _grant_acl(self, data: dict[str, Any]) -> None:
        self._request("PUT", "/access/acl", data=data)

    # --- internals -----------------------------------------------------------

    def _request(self, method: str, path: str, *, data: dict[str, Any] | None = None) -> Any:
        headers = {}
        if method in _WRITE_METHODS and self._csrf:
            headers["CSRFPreventionToken"] = self._csrf
        try:
            resp = self._client.request(method, path, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiError(f"{method} {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ApiError(
                f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise ApiError(f"{method} {path}: non-JSON response") from exc
        return body.get("data") if isinstance(body, dict) else body

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class PveProvisioner(_Provisioner):
    """Quick-setup provisioning against PVE (port 8006)."""

    _cookie_name = "PVEAuthCookie"
    _token_create_params = {"privsep": 1}  # privilege-separated token (own ACLs)

    def __init__(self, host: str, port: int = 8006, verify: bool = False, **kwargs: Any):
        super().__init__(host, port, verify, **kwargs)

    def grant_token_role(self, token_id: str, role_id: str = ROLE_ID, path: str = "/") -> None:
        """ACL the token to ``role_id`` at ``path`` (privsep tokens start with no privs)."""
        self._grant_acl({"path": path, "roles": role_id, "tokens": token_id, "propagate": 1})

    def provision_token(
        self, username: str, password: str, token_name: str = "joulenap"
    ) -> CreatedToken:
        """Full quick-setup flow: log in, ensure the role, create the token, grant it."""
        self.login(username, password)
        self.ensure_role(ROLE_ID, ROLE_PRIVS)
        token = self.create_token(username, token_name)
        self.grant_token_role(token.token_id)
        return token


class PbsProvisioner(_Provisioner):
    """Quick-setup provisioning against PBS (port 8007)."""

    _cookie_name = "PBSAuthCookie"

    def __init__(self, host: str, port: int = 8007, verify: bool = False, **kwargs: Any):
        super().__init__(host, port, verify, **kwargs)

    def grant_acl(self, token_id: str, path: str, role: str) -> None:
        """ACL the token to ``role`` at ``path``. PBS uses the singular ``role`` /
        ``auth-id`` parameters (vs PVE's ``roles`` / ``tokens``)."""
        self._grant_acl({"path": path, "role": role, "auth-id": token_id, "propagate": 1})

    def provision_token(
        self, username: str, password: str, datastore: str, token_name: str = "joulenap"
    ) -> CreatedToken:
        """Full quick-setup flow: log in, create the token, grant it built-in roles —
        DatastoreAdmin on the datastore (GC + status) and Audit on /system (node load).
        No role creation — PBS doesn't expose role management via the API."""
        self.login(username, password)
        token = self.create_token(username, token_name)
        self.grant_acl(token.token_id, f"/datastore/{datastore}", PBS_DATASTORE_ROLE)
        self.grant_acl(token.token_id, "/system", PBS_SYSTEM_ROLE)
        return token
