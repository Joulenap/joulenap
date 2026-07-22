"""GET/PUT /api/config — read the redacted config; apply changes and re-arm.

This is the design's "Apply changes" action: validate the whole config, persist it, then
re-arm the scheduler so a new schedule/enabled flag takes effect immediately.
"""

from __future__ import annotations

import secrets
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError

from ..config import (
    Config,
    RedactionError,
    deep_merge,
    enforce_server_managed,
    redacted_dict,
    restore_secrets,
)
from ..connectors.errors import WolError
from ..connectors.wol import normalize_mac
from ..core.config_store import ConfigStore
from ..core.scheduler import validate_cron
from .deps import Scheduler, get_config_store, get_scheduler, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["config"])


@router.get("/config")
def get_config(store: ConfigStore = Depends(get_config_store)) -> dict[str, Any]:
    return redacted_dict(store.config)


@router.put("/config")
def put_config(
    incoming: dict[str, Any],
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    return _apply_config(incoming, store, scheduler)


def _apply_config(
    incoming: dict[str, Any], store: ConfigStore, scheduler: Scheduler
) -> dict[str, Any]:
    """Validate + persist an incoming (partial, possibly redacted) config and re-arm.

    Shared by PUT /api/config and PUT /api/config/yaml so the YAML editor goes through the
    exact same validation and secret-restoration path as the settings forms.
    """
    # Deep-merge over the stored config so PUT means "apply these changes", not "replace
    # everything": an omitted section/field keeps its current value (a partial body can no
    # longer wipe secrets). Then resolve any ***REDACTED*** the client echoed back, and force
    # server-managed secrets (secret_key, password_hash, api_key) to the stored values.
    base = store.config.model_dump(mode="python")
    try:
        merged = restore_secrets(deep_merge(base, incoming), store.config)
    except RedactionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    merged = enforce_server_managed(merged, store.config)
    try:
        new_config = Config.model_validate(merged)
    except ValidationError as exc:
        # 422 to mirror FastAPI's own body-validation responses (literal avoids the
        # deprecated HTTP_422_UNPROCESSABLE_ENTITY constant name).
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc

    # Reject a newly-set invalid cron schedule before it can be persisted (BE-B1): an
    # unparseable string would 500 the rearm below and then brick every restart. Only
    # *changed* values are checked so a legacy on-disk schedule carried through an
    # unrelated edit doesn't lock the user out of saving (the rearm guards tolerate it).
    old = store.config
    for label, new_val, old_val in (
        ("backup.schedule", new_config.backup.schedule, old.backup.schedule),
        (
            "maintenance.verify.schedule",
            new_config.maintenance.verify.schedule,
            old.maintenance.verify.schedule,
        ),
    ):
        if new_val and new_val != old_val:
            try:
                validate_cron(new_val)
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid {label} {new_val!r}: {exc}"
                ) from exc

    # Reject a newly-set malformed WoL MAC before persisting (BE-C2), reusing the exact
    # WoL parser so "fails at save" == "fails at wake time". Changed-only + non-empty, same
    # as the cron block: an empty MAC is the wizard's unconfigured state, and a legacy bad
    # MAC on disk carried through an unrelated edit stays saveable (fails later at wake, as
    # today) rather than locking the user out of Settings. Not a pydantic validator, so it
    # never runs at load time and can't brick startup (the BE-B1 lesson).
    if new_config.pbs.mac and new_config.pbs.mac != old.pbs.mac:
        try:
            normalize_mac(new_config.pbs.mac)
        except WolError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid pbs.mac {new_config.pbs.mac!r}: {exc}"
            ) from exc

    try:
        store.replace(new_config)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    scheduler.rearm(new_config)
    return redacted_dict(new_config)


# --- raw YAML editing (Settings -> Advanced) ---------------------------------
#
# The editor is served the *redacted* config re-serialised with the same dumper save_config
# uses, so the text matches the on-disk file's shape without ever sending secrets to the
# browser. Saving goes through _apply_config, i.e. the same merge/restore/validate path as
# the settings forms: a key the user deletes keeps its stored value rather than being wiped.


class YamlBody(BaseModel):
    yaml: str


def _dump_yaml(cfg: Any) -> str:
    return yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _yaml_error(message: str, line: int | None = None) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": message, "line": line})


def _flatten(detail: Any) -> str:
    """Turn _apply_config's error detail into text the editor can show.

    Pydantic's ``errors()`` list becomes one ``field.path: message`` per line; anything else
    (the cron/MAC guards) is already a plain string.
    ponytail: no loc -> line mapping — only YAML syntax errors carry a line number.
    """
    if isinstance(detail, list):
        return "\n".join(
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}".lstrip(": ")
            for e in detail
        )
    return str(detail)


@router.get("/config/yaml")
def get_config_yaml(store: ConfigStore = Depends(get_config_store)) -> dict[str, str]:
    return {"yaml": _dump_yaml(redacted_dict(store.config))}


@router.put("/config/yaml")
def put_config_yaml(
    body: YamlBody,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    try:
        incoming = yaml.safe_load(body.yaml) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise _yaml_error(str(exc), mark.line + 1 if mark else None) from exc
    if not isinstance(incoming, dict):
        raise _yaml_error(
            f"The document must be a YAML mapping, got {type(incoming).__name__}."
        )
    try:
        return _apply_config(incoming, store, scheduler)
    except HTTPException as exc:
        # Re-shape validation failures into the editor's {message, line} contract; PUT
        # /api/config keeps its own. A 500 (unwritable config.yaml) passes through as-is.
        if exc.status_code != 422:
            raise
        raise _yaml_error(_flatten(exc.detail)) from exc


@router.post("/config/api-key", status_code=status.HTTP_200_OK)
def generate_api_key(store: ConfigStore = Depends(get_config_store)) -> dict[str, str]:
    """Generate (or rotate) the dashboard integration key; returns it once."""
    key = secrets.token_urlsafe(32)
    try:
        store.update(lambda c: setattr(c.app, "api_key", key))
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    return {"api_key": key}


@router.delete("/config/api-key", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(store: ConfigStore = Depends(get_config_store)) -> None:
    """Clear the dashboard integration key (disables GET /api/dashboard)."""
    try:
        store.update(lambda c: setattr(c.app, "api_key", ""))
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
