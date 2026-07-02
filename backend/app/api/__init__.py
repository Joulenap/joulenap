"""Aggregate API router: auth (M1), the M4 status/config/guests/jobs/logs routers, and
the M5 setup-wizard router."""

from fastapi import APIRouter

from . import auth, config, guests, jobs, logs, notify, power, scheduler, status, wizard, wol

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(status.router)
api_router.include_router(config.router)
api_router.include_router(guests.router)
api_router.include_router(scheduler.router)
api_router.include_router(jobs.router)
api_router.include_router(power.router)
api_router.include_router(wol.router)
api_router.include_router(notify.router)
api_router.include_router(logs.router)
api_router.include_router(wizard.router)

__all__ = ["api_router"]
