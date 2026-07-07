"""Shared FastAPI dependencies for the API routers.

The app holds its long-lived collaborators on ``app.state`` (set in the lifespan); these
helpers expose them to routers, and re-export the auth/config dependencies so routers
have a single import site.
"""

from __future__ import annotations

from fastapi import Request

from ..core.scheduler import Scheduler
from ..core.security import CurrentUser, get_config_store, require_auth
from ..jobs import JobService
from ..notify import NotificationService

__all__ = [
    "CurrentUser",
    "JobService",
    "NotificationService",
    "Scheduler",
    "get_config_store",
    "get_job_service",
    "get_notifier",
    "get_scheduler",
    "require_auth",
]


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service


def get_notifier(request: Request) -> NotificationService:
    return request.app.state.notifier
