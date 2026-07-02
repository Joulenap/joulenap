"""FastAPI application entrypoint.

Milestone 1 wires the app skeleton: health check, static frontend serving and the
``/api`` router (auth in M1; status/config/guests/etc. land in later milestones).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .api import api_router
from .core.config_store import ConfigStore
from .core.scheduler import Scheduler
from .db import init_db, session_scope
from .db.startup import sweep_orphaned_runs
from .jobs import JobService
from .notify import NotificationService

log = logging.getLogger("joulenap.main")

def _frontend_dir() -> Path:
    """Directory of the built SPA (Vite output) served as static files.

    ``JOULENAP_FRONTEND_DIR`` wins (the Docker image sets it, since the installed package
    lives in site-packages and can't resolve the repo layout). Otherwise fall back to the
    repo checkout's ``frontend/dist``. When the dir is absent (dev without a build, or
    tests) the mount is skipped and only the API is served.
    """
    env = os.getenv("JOULENAP_FRONTEND_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Create the SQLite schema before serving requests, then start the in-process
    # scheduler and arm the backup job from config.
    init_db()
    # A previous process may have died mid-cycle, leaving runs stuck RUNNING; fail them
    # so the dashboard doesn't show a run that never finishes.
    with session_scope() as session:
        swept = sweep_orphaned_runs(session)
    if swept:
        log.warning("Marked %d interrupted run(s) as failed at startup", swept)
    store: ConfigStore = app.state.config_store
    service = JobService(store)
    scheduler = Scheduler(
        service.submit_backup,
        service.run_prune,
        service.submit_verify,
        timezone=store.config.app.timezone,
    )
    scheduler.start()
    scheduler.rearm(store.config)
    scheduler.arm_prune()
    app.state.job_service = service
    app.state.scheduler = scheduler
    app.state.notifier = NotificationService()
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    # Load (or first-run create) config before building the app: the session
    # middleware needs the signing key, and routers read config via app.state.
    store = ConfigStore.load_or_create()

    app = FastAPI(title="Joulenap", version=__version__, lifespan=lifespan)
    app.state.config_store = store

    # Signed session cookie. https_only stays off for LAN/HTTP; same_site=lax is
    # fine for a same-origin SPA.
    app.add_middleware(
        SessionMiddleware,
        secret_key=store.config.app.secret_key,
        session_cookie="joulenap_session",
        same_site="lax",
        https_only=False,
    )

    @app.get("/api/health", tags=["meta"])
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    app.include_router(api_router)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the SPA. Unknown non-/api paths fall back to index.html (client routing)."""
    frontend_dir = _frontend_dir()
    if not frontend_dir.exists():
        return

    index = frontend_dir / "index.html"

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(index)

    # Mounted last so it doesn't shadow /api/* routes registered above.
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")


def run() -> None:
    """Console-script entrypoint (`joulenap`).

    Uses uvicorn's factory mode so the app — and its startup I/O (config load/create,
    secret_key seeding) — is built only when the server actually boots, never on import.
    The bind port comes from ``app.port`` (config-driven); load/create the
    config here so a first run seeds config.yaml before the factory reads it again.
    """
    import uvicorn

    port = ConfigStore.load_or_create().config.app.port
    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
