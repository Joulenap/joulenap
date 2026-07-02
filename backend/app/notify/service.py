"""Deliver notifications through Apprise.

One engine (apprise) fans a single message out to every configured channel. The Apprise
object is built per-send from the live config, so a config change takes effect on the next
notification with no re-arming. The engine factory is injectable so tests can assert what
would be sent without hitting any real service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import apprise

from ..config import Config
from ..db.models import Run, RunStatus
from .apprise_urls import build_urls
from .messages import build_run_message, build_test_message

if TYPE_CHECKING:
    from ..connectors.pbs import DatastoreStatus


@dataclass
class NotifyReport:
    """Outcome of a delivery attempt — drives the test endpoint's HTTP response."""

    sent: bool
    channels: int
    skipped: bool = False
    reason: str | None = None
    error: str | None = None


class NotificationService:
    def __init__(self, apprise_factory: Callable[[], Any] = apprise.Apprise):
        self._apprise_factory = apprise_factory

    def send_run_result(
        self, config: Config, run: Run, datastore: DatastoreStatus | None = None
    ) -> NotifyReport:
        """Notify the run outcome, honouring the on_success / on_failure routing toggles."""
        n = config.notifications
        if run.status == RunStatus.SUCCESS and not n.on_success:
            return NotifyReport(sent=False, channels=0, skipped=True, reason="on_success disabled")
        if run.status in (RunStatus.FAILURE, RunStatus.ABORTED) and not n.on_failure:
            return NotifyReport(sent=False, channels=0, skipped=True, reason="on_failure disabled")
        title, body = build_run_message(config, run, datastore)
        return self._dispatch(build_urls(n), title, body)

    def send_test(self, config: Config) -> NotifyReport:
        """Send a test message to every configured channel, ignoring the routing toggles."""
        title, body = build_test_message(config)
        return self._dispatch(build_urls(config.notifications), title, body)

    def _dispatch(self, urls: list[str], title: str, body: str) -> NotifyReport:
        if not urls:
            return NotifyReport(sent=False, channels=0, reason="no_channels")
        engine = self._apprise_factory()
        added = sum(1 for url in urls if engine.add(url))
        if added == 0:
            return NotifyReport(sent=False, channels=0, error="no valid notification URLs")
        ok = bool(engine.notify(title=title, body=body))
        return NotifyReport(
            sent=ok, channels=added, error=None if ok else "delivery failed"
        )
