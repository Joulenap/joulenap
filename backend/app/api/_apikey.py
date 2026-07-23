"""Shared API-key check for the read-only, session-less endpoints.

Both `/api/dashboard` (dashboard widgets) and `/metrics` (Prometheus) are polled by
machines that can't hold a login session, so they authenticate with the single
``app.api_key`` instead — sent as an ``X-API-Key`` header, or as a ``?key=`` query
param for clients that can't set custom headers (Prometheus's ``params:``, older
dashboard widgets).
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from ..core.config_store import ConfigStore


def authorize_api_key(request: Request, store: ConfigStore) -> None:
    """Raise unless the request carries the configured API key.

    403 when no key is configured (the integration is off) vs 401 for a wrong key, so a
    scraper can tell "not enabled here" from "my credential is wrong".
    """
    key = store.config.app.api_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read-only API access is disabled (no API key configured)",
        )
    provided = request.headers.get("X-API-Key") or request.query_params.get("key") or ""
    if not secrets.compare_digest(provided.encode("utf-8"), key.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key"
        )
