"""POST /api/notify/test — send a one-off test notification to verify the channels.

Unlike a run notification this ignores the on_success / on_failure routing toggles: it
fans out to every configured channel so the user can confirm their setup from the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..core.config_store import ConfigStore
from .deps import get_config_store, get_notifier, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["notify"])


class NotifyTestResult(BaseModel):
    sent: bool
    channels: int


@router.post("/notify/test", response_model=NotifyTestResult)
def notify_test(
    store: ConfigStore = Depends(get_config_store),
    notifier=Depends(get_notifier),
) -> NotifyTestResult:
    report = notifier.send_test(store.config)
    if report.reason == "no_channels":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No notification channels configured",
        )
    if not report.sent:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=report.error or "Notification delivery failed",
        )
    return NotifyTestResult(sent=True, channels=report.channels)
