"""CRUD for the PVE/PBS devices, plus their connection test, power buttons and ad-hoc
maintenance runs.

Devices are the Settings > Devices tab. Two things here are load-bearing beyond plain CRUD:

* **Removal is guarded.** Deleting a device a route still points at would leave the config
  invalid; rather than cascade-deleting the user's routes behind their back, we 409 and name
  them, so the user decides what to do with each.
* **Secrets round-trip by id.** ``GET`` masks tokens as ``***REDACTED***``; a ``PUT`` that
  echoes the placeholder back keeps the stored value. That resolution is per-device here (we
  know exactly which one is being edited), so reordering the list can never map a placeholder
  onto the wrong device's secret.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError

from ..config import PbsDevice, PveDevice, RedactionError, redact, restore_secrets_from
from ..connectors.errors import ConnectorError, WolError
from ..core.config_store import ConfigStore
from ..db.models import RunTrigger
from ..jobs import AlreadyRunningError
from ._config_edit import save_section, validation_error
from .deps import (
    JobService,
    Scheduler,
    get_config_store,
    get_job_service,
    get_scheduler,
    require_auth,
)

router = APIRouter(prefix="/devices", dependencies=[Depends(require_auth)], tags=["devices"])

#: url segment -> (config section, model). One pair of handlers serves both kinds; they
#: differ only in the model that validates the body and in what "test" means.
_KINDS: dict[str, tuple[str, type[PveDevice] | type[PbsDevice]]] = {
    "pves": ("pves", PveDevice),
    "pbss": ("pbss", PbsDevice),
}


def _kind(kind: str) -> tuple[str, type[PveDevice] | type[PbsDevice]]:
    if kind not in _KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device kind must be 'pves' or 'pbss'"
        )
    return _KINDS[kind]


def _dump(store: ConfigStore, section: str) -> list[dict[str, Any]]:
    return [d.model_dump(mode="python") for d in getattr(store.config, section)]


def _find(store: ConfigStore, section: str, device_id: str) -> int:
    for i, device in enumerate(getattr(store.config, section)):
        if device.id == device_id:
            return i
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No device '{device_id}'")


def _routes_using(store: ConfigStore, section: str, device_id: str) -> list[str]:
    """Names of the routes that would break if this device went away."""
    names = []
    for route in store.config.routes:
        if section == "pbss":
            used = device_id in (route.target, route.source_pbs)
        else:
            used = any(s.pve == device_id for s in route.sources)
        if used:
            names.append(route.name or route.id)
    return names


def _pbs(store: ConfigStore, pbs_id: str) -> PbsDevice:
    device = next((p for p in store.config.pbss if p.id == pbs_id), None)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No pbs '{pbs_id}'")
    return device


# --- CRUD --------------------------------------------------------------------


@router.get("")
def list_devices(store: ConfigStore = Depends(get_config_store)) -> dict[str, Any]:
    """Both device lists, secrets masked."""
    return {section: redact(_dump(store, section)) for section, _ in _KINDS.values()}


@router.post("/{kind}", status_code=status.HTTP_201_CREATED)
def create_device(
    kind: str,
    body: dict[str, Any],
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    section, model = _kind(kind)
    # Against an empty stored mapping: a *new* device has no secrets to restore, so any
    # ***REDACTED*** in the body — a "duplicate this device" action, or a body copy-pasted
    # from GET /api/devices — fails loudly here instead of being stored as the literal
    # placeholder, which GET then re-masks so the corruption is invisible until a
    # connection test fails with a 502 that gives no hint why.
    device = _validate(model, _resolve(body, {}))
    if any(d.id == device.id for d in getattr(store.config, section)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Device '{device.id}' already exists"
        )
    save_section(
        store, scheduler, section, [*_dump(store, section), device.model_dump(mode="python")]
    )
    return redact(device.model_dump(mode="python"))


@router.put("/{kind}/{device_id}")
def update_device(
    kind: str,
    device_id: str,
    body: dict[str, Any],
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    section, model = _kind(kind)
    index = _find(store, section, device_id)
    # Resolve ***REDACTED*** against *this* device's stored values: the client only ever
    # echoes back a secret it didn't change.
    device = _validate(model, _resolve(body, _dump(store, section)[index]))
    devices = _dump(store, section)
    devices[index] = device.model_dump(mode="python")
    save_section(store, scheduler, section, devices)
    return redact(device.model_dump(mode="python"))


@router.delete("/{kind}/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    kind: str,
    device_id: str,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> None:
    section, _model = _kind(kind)
    index = _find(store, section, device_id)
    used_by = _routes_using(store, section, device_id)
    if used_by:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"'{device_id}' is used by {len(used_by)} route(s). Change or delete them "
                    "first — removing the device would break them."
                ),
                "routes": used_by,
            },
        )
    devices = _dump(store, section)
    del devices[index]
    save_section(store, scheduler, section, devices)


# --- test --------------------------------------------------------------------


class DeviceTestResult(BaseModel):
    ok: bool
    #: Free-form one-liner for the card's status row (a version string, a datastore usage).
    detail: str = ""


@router.post("/{kind}/{device_id}/test", response_model=DeviceTestResult)
def test_device(
    kind: str,
    device_id: str,
    store: ConfigStore = Depends(get_config_store),
    job_service: JobService = Depends(get_job_service),
) -> DeviceTestResult:
    """Talk to the device with its stored credentials.

    A failure is reported as a 502, not a 200 with ``ok: false`` — the UI shows the reason,
    and "could not reach it" is genuinely an upstream problem, not a result.
    """
    section, _model = _kind(kind)
    index = _find(store, section, device_id)
    device = getattr(store.config, section)[index]
    try:
        if section == "pves":
            with job_service.deps.connect_pve(device) as client:
                nodes = client.list_cluster_guests()
            return DeviceTestResult(ok=True, detail=f"{len(nodes)} guest(s) visible")
        with job_service.deps.connect_pbs(device) as client:
            ds = client.datastore_status()
        return DeviceTestResult(
            ok=True, detail=f"datastore {device.datastore}: {ds.used_pct}% used"
        )
    except ConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# --- power -------------------------------------------------------------------


class PowerRequest(BaseModel):
    action: Literal["wake", "poweroff"]


class PowerResult(BaseModel):
    ok: bool


@router.post("/pbss/{pbs_id}/power", response_model=PowerResult)
def power(
    pbs_id: str,
    body: PowerRequest,
    store: ConfigStore = Depends(get_config_store),
    job_service: JobService = Depends(get_job_service),
) -> PowerResult:
    """The topology card's ⏻ button: Wake-on-LAN, or the SSH power-off."""
    device = _pbs(store, pbs_id)
    if not device.managed_power:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Joulenap does not manage power for '{pbs_id}' (managed_power: false), so it "
                "can neither wake nor shut it down. Turn on managed power for this device if "
                "you want the button to work."
            ),
        )
    if body.action == "wake":
        if not device.mac:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No MAC address configured for '{pbs_id}'",
            )
        try:
            job_service.lease.wake(device)
        except WolError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        return PowerResult(ok=True)

    # Power-off, under the single-run lock. `exclusive()` exists for exactly this: a run
    # holds that lock for its whole life, so while we hold it none can start — and
    # `power_off_now` has no idle-wait and no refcount check by design ("a click means
    # now"). Without the lock the check is a check-then-act: a scheduled route starts in
    # the gap, `_bring_up` probes, finds the box still up, skips the Wake-on-LAN, vzdump
    # begins, and the SSH poweroff lands mid-backup. The lease's own self-healing can't
    # help there — the probe has already happened.
    # ponytail: the lock is held for the seconds an SSH connect takes, so a scheduled run
    # firing in that window waits its turn instead of being rejected. Fine for a button.
    try:
        with job_service.exclusive():
            # Belt and braces: a lease is only ever taken inside the lock we now hold, so
            # this cannot fire in production — but it names the box when it does.
            if job_service.lease.state(pbs_id).holders:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A run is using '{pbs_id}'; cannot power it off",
                )
            job_service.lease.power_off_now(device)
    except AlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PowerResult(ok=True)


# --- ad-hoc maintenance ------------------------------------------------------


class MaintenanceOptions(BaseModel):
    keep_on: bool = False


class MaintenanceQueued(BaseModel):
    pbs_id: str
    action: str
    queued: int


@router.post("/pbss/{pbs_id}/{action}", status_code=status.HTTP_202_ACCEPTED)
def run_maintenance(
    pbs_id: str,
    action: str,
    opts: MaintenanceOptions | None = None,
    job_service: JobService = Depends(get_job_service),
) -> MaintenanceQueued:
    """Queue a one-off GC or verify on this PBS ("Run GC" / "Run verify" on the homepage).

    Deliberately not tied to a route: you reach for these after a restore or a disk scare,
    and a throwaway route would put a phantom entry in the topology.
    """
    try:
        ahead = job_service.run_maintenance(
            pbs_id,
            action,
            RunTrigger.MANUAL,
            power_off=not (opts or MaintenanceOptions()).keep_on,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No pbs '{pbs_id}'"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown action '{action}'"
        ) from exc
    except AlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MaintenanceQueued(pbs_id=pbs_id, action=action, queued=ahead)


# --- helpers -----------------------------------------------------------------


def _resolve(body: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """Fill in the ***REDACTED*** placeholders the client echoed back, 422 if one can't be
    resolved. ``stored`` is the device being edited, or ``{}`` for a create."""
    try:
        return restore_secrets_from(body, stored)
    except RedactionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate(model: type[PveDevice] | type[PbsDevice], body: dict[str, Any]):
    """Validate a device body by hand rather than as a typed parameter: the handlers are
    shared between the two kinds, so which model applies is only known at call time."""
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise validation_error(exc) from exc
