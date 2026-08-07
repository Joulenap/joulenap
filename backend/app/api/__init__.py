"""Aggregate API router: auth, the route/device CRUD the UI is built on, the
status/dashboard/logs read models, and the setup-wizard helpers."""

from fastapi import APIRouter

from . import (
    auth,
    config,
    dashboard,
    devices,
    guests,
    jobs,
    logs,
    notify,
    routes,
    scheduler,
    status,
    update,
    wizard,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(status.router)
api_router.include_router(dashboard.router)
api_router.include_router(config.router)
api_router.include_router(routes.router)
api_router.include_router(devices.router)
api_router.include_router(guests.router)
api_router.include_router(scheduler.router)
api_router.include_router(jobs.router)
api_router.include_router(notify.router)
api_router.include_router(logs.router)
api_router.include_router(wizard.router)
api_router.include_router(update.router)

__all__ = ["api_router"]
