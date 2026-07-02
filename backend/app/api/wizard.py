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
from ..connectors.errors import ConnectorError
from ..core import wizard
from ..core.config_store import ConfigStore
from .deps import get_config_store, require_auth

router = APIRouter(prefix="/wizard", dependencies=[Depends(require_auth)], tags=["wizard"])

_KEY_FILENAME = "id_ed25519"


def _connector_call(func, **kwargs) -> Any:
    """Run a wizard helper, mapping connector failures to 502 Bad Gateway."""
    try:
        return func(**kwargs)
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
    token_name: str = "joulenap"


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
        token_name=body.token_name,
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
    # Always write into the (writable, auto-created) data dir; the frontend points
    # config.pbs.ssh_key_path at the returned path.
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


# --- reset setup -------------------------------------------------------------

# Connection-identity fields the wizard populates. Reset blanks exactly these so the wizard
# starts fresh, while tuning left elsewhere (ports, TLS, wake timeouts, backup safety,
# notifications, schedule, the admin account) is preserved. The generated SSH key file is
# intentionally kept — only its reference is cleared here.
_PVE_RESET = ("host", "node", "api_token_id", "api_token_secret", "storage_id")
_PBS_RESET = (
    "host",
    "datastore",
    "fingerprint",
    "api_token_id",
    "api_token_secret",
    "mac",
    "wol_broadcast_iface",
)


@router.post("/reset")
def reset_setup(store: ConfigStore = Depends(get_config_store)) -> dict[str, bool]:
    """Clear the saved PVE/PBS connection so the setup wizard restarts from scratch.

    Only the connection-identity fields are wiped; everything else (schedule, retention,
    notifications, backup-safety tuning, the login account) is left untouched."""

    def clear(cfg: Any) -> None:
        for field in _PVE_RESET:
            setattr(cfg.pve, field, "")
        for field in _PBS_RESET:
            setattr(cfg.pbs, field, "")

    store.update(clear)
    return {"ok": True}
