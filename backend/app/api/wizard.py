"""Setup wizard endpoints (docs/CONFIG-WIZARD.md).

Discovery + provisioning actions the wizard cards call. All are stateless: they return
discovered values for the frontend to assemble and save via PUT /api/config; only
ssh/keygen writes to disk (the private key). Auth-guarded like the rest of /api.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import paths
from ..connectors import net
from ..connectors.errors import ConnectorError, TokenExistsError
from ..connectors.provision import pbs_token_name
from ..core import wizard
from .deps import require_auth

router = APIRouter(prefix="/wizard", dependencies=[Depends(require_auth)], tags=["wizard"])

_KEY_FILENAME = "id_ed25519"


def _connector_call(func, **kwargs) -> Any:
    """Run a wizard helper, mapping connector failures to 502 Bad Gateway.

    ``TokenExistsError`` is the exception: nothing failed upstream, we declined to replace a
    token the user has not agreed to lose. 409 so the wizard can tell it apart from a real
    connection problem and offer to go ahead.
    """
    try:
        return func(**kwargs)
    except TokenExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# --- PVE connect -------------------------------------------------------------


class PveConnectRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=8006, ge=1, le=65535)
    verify_tls: bool = False
    mode: Literal["token", "root"] = "token"
    # token mode
    api_token_id: str | None = None
    api_token_secret: str | None = None
    # root (quick setup) mode
    username: str | None = None
    password: str | None = None
    token_name: str = "joulenap"
    #: The user's answer to the 409 this endpoint raises when ``token_name`` is taken.
    replace_token: bool = False


@router.post("/pve/connect")
def pve_connect(body: PveConnectRequest) -> dict[str, Any]:
    return _connector_call(
        wizard.pve_connect,
        host=body.host,
        port=body.port,
        verify_tls=body.verify_tls,
        mode=body.mode,
        token_id=body.api_token_id,
        token_secret=body.api_token_secret,
        username=body.username,
        password=body.password,
        token_name=body.token_name,
        replace_token=body.replace_token,
    )


# --- derive PBS from storage -------------------------------------------------


class StorageDeriveRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=8006, ge=1, le=65535)
    verify_tls: bool = False
    api_token_id: str = Field(min_length=1)
    api_token_secret: str = Field(min_length=1)
    storage_id: str = Field(min_length=1)


@router.post("/storage/derive")
def storage_derive(body: StorageDeriveRequest) -> dict[str, Any]:
    return _connector_call(
        wizard.storage_derive,
        host=body.host,
        port=body.port,
        verify_tls=body.verify_tls,
        token_id=body.api_token_id,
        token_secret=body.api_token_secret,
        storage_id=body.storage_id,
    )


# --- PBS reachability + fingerprint ------------------------------------------


class PbsCheckRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=8007, ge=1, le=65535)


@router.post("/pbs/check")
def pbs_check(body: PbsCheckRequest) -> dict[str, Any]:
    return wizard.pbs_check(host=body.host, port=body.port)


# --- PBS token auto-provision (quick setup) ----------------------------------


class PbsProvisionRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=8007, ge=1, le=65535)
    verify_tls: bool = False
    username: str = "root@pam"
    password: str = Field(min_length=1)
    datastore: str = Field(min_length=1)
    #: Left unset by the UI: the name is derived from the datastore, so two devices on one
    #: backup server never contend for it. Still overridable for anyone driving the API.
    token_name: str | None = None
    fingerprint: str = ""
    #: The user's answer to the 409 this endpoint raises when ``token_name`` is taken.
    replace_token: bool = False


@router.post("/pbs/provision")
def pbs_provision(body: PbsProvisionRequest) -> dict[str, Any]:
    return _connector_call(
        wizard.pbs_provision,
        host=body.host,
        port=body.port,
        verify_tls=body.verify_tls,
        username=body.username,
        password=body.password,
        datastore=body.datastore,
        token_name=body.token_name or pbs_token_name(body.datastore),
        fingerprint=body.fingerprint,
        replace_token=body.replace_token,
    )


# --- PBS sync grants on an existing token ------------------------------------


class PbsGrantSyncRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=8007, ge=1, le=65535)
    verify_tls: bool = False
    username: str = "root@pam"
    password: str = Field(min_length=1)
    api_token_id: str = Field(min_length=1)
    fingerprint: str = ""


@router.post("/pbs/grant-sync")
def pbs_grant_sync(body: PbsGrantSyncRequest) -> dict[str, Any]:
    """Grant the /remote roles a Sync route needs to a PBS token that already exists."""
    return _connector_call(
        wizard.pbs_grant_sync,
        host=body.host,
        port=body.port,
        verify_tls=body.verify_tls,
        username=body.username,
        password=body.password,
        token_id=body.api_token_id,
        fingerprint=body.fingerprint,
    )


# --- local network interfaces (for the WoL interface picker) -----------------


@router.get("/interfaces")
def interfaces() -> list[dict[str, str]]:
    """List the host's IPv4 interfaces so the wizard can offer a WoL interface dropdown."""
    return [
        {"name": i.name, "address": i.address, "netmask": i.netmask, "broadcast": i.broadcast}
        for i in net.list_interfaces()
    ]


# --- Wake-on-LAN MAC detection -----------------------------------------------


class DetectMacRequest(BaseModel):
    host: str = Field(min_length=1)


@router.post("/wol/detect-mac")
def detect_mac(body: DetectMacRequest) -> dict[str, Any]:
    return wizard.wol_detect_mac(host=body.host)


# --- SSH key generation + install --------------------------------------------


@router.post("/ssh/keygen")
def ssh_keygen() -> dict[str, Any]:
    # Always write into the (writable, auto-created) data dir; the frontend points the
    # device's ssh_key_path at the returned path.
    key_path = paths.data_dir() / _KEY_FILENAME
    return _connector_call(wizard.ssh_keygen, key_path=key_path)


class SshInstallRequest(BaseModel):
    host: str = Field(min_length=1)
    user: str = "root"
    password: str = Field(min_length=1)
    public_key: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)


@router.post("/ssh/install")
def ssh_install(body: SshInstallRequest) -> dict[str, Any]:
    return _connector_call(
        wizard.ssh_install,
        host=body.host,
        user=body.user,
        password=body.password,
        public_key=body.public_key,
        port=body.port,
    )


class SshHostkeyRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)


@router.post("/ssh/hostkey")
def ssh_hostkey(body: SshHostkeyRequest) -> dict[str, Any]:
    return _connector_call(wizard.ssh_hostkey, host=body.host, port=body.port)


class SshTrustRequest(BaseModel):
    host: str = Field(min_length=1)
    key_type: str = Field(min_length=1)
    key_base64: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)


@router.post("/ssh/trust")
def ssh_trust(body: SshTrustRequest) -> dict[str, Any]:
    return _connector_call(
        wizard.ssh_trust, host=body.host, key_type=body.key_type,
        key_base64=body.key_base64, port=body.port,
    )


# --- Wake-on-LAN smoke test --------------------------------------------------
#
# Stateless like the rest of this router: the wizard tests a MAC it has just detected,
# before there is a device to save it on. Waking a *configured* PBS is
# POST /api/devices/pbss/{id}/power instead.


class WolTestRequest(BaseModel):
    mac: str = Field(min_length=1)
    # The PBS's address, so the packet goes to that subnet's directed broadcast rather than
    # the whole network. Optional: pre-setup we may not know it yet.
    host: str = ""
    iface: str = ""


@router.post("/wol/test")
def wol_test(body: WolTestRequest) -> dict[str, Any]:
    return _connector_call(
        wizard.wol_test, mac=body.mac, host=body.host, iface=body.iface
    )


# The per-device flows (M12) are orchestrated by the frontend wizard out of these same
# stateless calls and finalised with POST /api/devices/{kind} — there is no wizard-owned
# device state to keep on this side. The one thing that had to move into the wizard's
# transient-root step is the /remote ACL grant a Sync route needs on the peer: PBS refuses
# ACL writes from a token, so `PbsProvisioner.provision_token` now makes it while it still
# holds the root ticket.
