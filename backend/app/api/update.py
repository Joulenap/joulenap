"""GET /api/update — is a newer Joulenap release out? Opt-in, cached, best-effort.

Deliberately *not* part of /api/health: that endpoint is the container HEALTHCHECK target
and must stay fast and offline-safe. This one is called by the footer instead, at most one
outbound request a day (in-memory cache), and only when ``app.update_check`` is on.
"""

from __future__ import annotations

import re
import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import __version__
from ..core.config_store import ConfigStore
from .deps import get_config_store, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["meta"])

_RELEASES_API = "https://api.github.com/repos/Joulenap/joulenap/releases/latest"
_RELEASES_PAGE = "https://github.com/Joulenap/joulenap/releases"
_TTL = 86400.0  # one check a day; the cache is memory-only, so a restart re-checks

# (checked_at monotonic, latest tag or "" when the check failed). Failures are cached too:
# offline means every page load would otherwise pay the request timeout.
_cache: tuple[float, str] | None = None


def _parse(v: str) -> tuple[int, ...]:
    """``"v0.4.4"`` -> ``(0, 4, 4)``. Stops at the first non-numeric part, so a suffixed
    tag ("0.5.0-beta") compares equal to its final release — we only publish finals."""
    out: list[int] = []
    for part in v.lstrip("vV").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group()))
    return tuple(out)


def _fetch_latest() -> str:
    """Latest release tag from GitHub, or "" on any failure — never raises."""
    try:
        resp = httpx.get(
            _RELEASES_API, timeout=4, headers={"Accept": "application/vnd.github+json"}
        )
        resp.raise_for_status()
        return str(resp.json().get("tag_name") or "")
    except Exception:  # noqa: BLE001 — offline, rate-limited or garbage all mean "don't know"
        return ""


class UpdateResponse(BaseModel):
    current: str
    latest: str = ""  # "" when unknown: check disabled, offline, or rate-limited
    update_available: bool = False
    url: str = _RELEASES_PAGE


@router.get("/update", response_model=UpdateResponse)
def get_update(store: ConfigStore = Depends(get_config_store)) -> UpdateResponse:
    global _cache
    if not store.config.app.update_check:
        return UpdateResponse(current=__version__)

    now = time.monotonic()
    if _cache is None or now - _cache[0] > _TTL:
        _cache = (now, _fetch_latest())
    latest = _cache[1]
    return UpdateResponse(
        current=__version__,
        latest=latest,
        update_available=bool(latest) and _parse(latest) > _parse(__version__),
    )
