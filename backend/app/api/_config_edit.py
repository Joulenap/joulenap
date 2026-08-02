"""One way to change a piece of config: validate the whole document, persist, re-arm.

The route and device endpoints all edit one list inside ``config.yaml``. Doing that by
mutating the live model would skip :meth:`Config._check_references` — the cross-checks that
catch a route pointing at a device that doesn't exist, or a duplicate id. So every edit is
"dump, replace one list, re-validate the whole thing", which is cheap (the config is a few
kilobytes) and makes an invalid combination impossible to persist rather than merely
unlikely.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ..config import Config
from ..core.config_store import ConfigStore
from ..core.scheduler import Scheduler, validate_cron


def save_section(
    store: ConfigStore, scheduler: Scheduler, section: str, value: list[dict[str, Any]]
) -> Config:
    """Replace ``config.<section>`` with ``value``, validate, persist and re-arm.

    Raises 422 with pydantic's own error list when the result doesn't validate, and 500 when
    the file can't be written (a read-only mount).
    """
    raw = store.config.model_dump(mode="python")
    raw[section] = value
    try:
        new_config = Config.model_validate(raw)
    except ValidationError as exc:
        # 422 mirrors FastAPI's own body-validation responses (the literal avoids the
        # deprecated HTTP_422_UNPROCESSABLE_ENTITY constant name).
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc
    check_route_crons(new_config, store.config)
    try:
        store.replace(new_config)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    scheduler.rearm(new_config)
    return new_config


def check_route_crons(new_config: Config, old_config: Config) -> None:
    """Reject a newly-set unparseable ``schedule.cron`` before it reaches disk (BE-B1).

    An invalid string wouldn't crash arming — ``_arm_route`` guards it — but the route would
    silently never fire, which is the failure mode hardest to notice. Only *changed* values
    are checked, so a legacy string already on disk doesn't lock the user out of saving an
    unrelated edit.
    """
    old = {r.id: r.schedule.cron for r in old_config.routes}
    for route in new_config.routes:
        cron = route.schedule.cron
        if not cron or cron == old.get(route.id):
            continue
        try:
            validate_cron(cron)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"route '{route.id}': invalid schedule.cron {cron!r}: {exc}",
            ) from exc
