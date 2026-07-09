"""POST /api/notify/test — send a one-off test notification to verify the channels.

Unlike a run notification this ignores the on_success / on_failure routing toggles: it
fans out to every configured channel so the user can confirm their setup from the UI.

Delivery failure is a *result*, not a transport error — the request itself succeeded — so the
endpoint always answers 200 and puts the per-channel outcome in the body. That is what lets
the UI say which channel broke and why, instead of one opaque "delivery failed".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.config_store import ConfigStore
from .deps import NotificationService, get_config_store, get_notifier, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["notify"])


class ChannelOutcome(BaseModel):
    channel: str
    ok: bool
    error: str | None = None


class NotifyTestResult(BaseModel):
    #: Empty when no channel is configured.
    channels: list[ChannelOutcome]


@router.post("/notify/test", response_model=NotifyTestResult)
def notify_test(
    store: ConfigStore = Depends(get_config_store),
    notifier: NotificationService = Depends(get_notifier),
) -> NotifyTestResult:
    report = notifier.send_test(store.config)
    return NotifyTestResult(
        channels=[ChannelOutcome(channel=r.channel, ok=r.ok, error=r.error) for r in report.results]
    )
