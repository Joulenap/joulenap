"""Deliver notifications through Apprise.

Apprise's own ``notify()`` fans out to every URL and collapses the outcome into a single
bool, discarding each channel's exception. We instead drive one engine per channel and
capture Apprise's log records for the duration of that send, so a failure can name itself
("DNS did not resolve", "401 Unauthorized") instead of just "delivery failed". Engines are
built per-send from the live config, so a config change takes effect on the next
notification with no re-arming. The engine factory is injectable so tests can assert what
would be sent without hitting any real service.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import apprise

from ..config import Config, NotificationsConfig
from ..db.models import Run, RunStatus
from .apprise_urls import Channel, build_channels
from .messages import build_run_message, build_test_message

if TYPE_CHECKING:
    from ..connectors.pbs import DatastoreStatus

logger = logging.getLogger(__name__)

#: Apprise messages can be long and end in a stack-trace-ish tail; keep the UI readable.
_MAX_ERROR_LEN = 300


@dataclass
class ChannelResult:
    """How one channel fared. ``error`` is absent when Apprise gave no reason."""

    channel: str
    ok: bool
    error: str | None = None


@dataclass
class NotifyReport:
    """Outcome of a delivery attempt — drives the test endpoint's HTTP response."""

    sent: bool
    channels: int
    skipped: bool = False
    reason: str | None = None
    error: str | None = None
    results: list[ChannelResult] = field(default_factory=list)


class _LogCapture(logging.Handler):
    """Collects the WARNING/ERROR records Apprise emits while one send is in flight."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def _captured_apprise_logs() -> Iterator[_LogCapture]:
    handler = _LogCapture()
    apprise_logger = logging.getLogger("apprise")
    apprise_logger.addHandler(handler)
    try:
        yield handler
    finally:
        apprise_logger.removeHandler(handler)


def _secrets(n: NotificationsConfig) -> list[str]:
    """Every configured secret, longest first so the longest match is scrubbed first."""
    values = [
        n.telegram.bot_token,
        n.email.smtp_password,
        n.email.smtp_user,
        n.discord.webhook_url,
        *n.custom_urls,
    ]
    return sorted((v for v in values if v), key=len, reverse=True)


def _scrub(message: str, secrets: list[str]) -> str:
    """Strip credentials out of an Apprise message before it reaches the UI or the log."""
    from urllib.parse import quote

    for secret in secrets:
        for form in (secret, quote(secret, safe="")):
            if form:
                message = message.replace(form, "***")
    # Catch anything left in ``//user:pass@host`` form (e.g. a secret we do not know about).
    message = re.sub(r"//[^/\s:@]+:[^/\s@]+@", "//***@", message)
    if len(message) > _MAX_ERROR_LEN:
        message = message[: _MAX_ERROR_LEN - 1] + "…"
    return message


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
        report = self._dispatch(build_channels(n), title, body, n)
        # A run notification has no UI: without this, a channel that silently stopped working
        # would never surface anywhere.
        for result in report.results:
            if not result.ok:
                logger.warning(
                    "notification channel %s failed: %s",
                    result.channel,
                    result.error or "no reason reported",
                )
        return report

    def send_test(self, config: Config) -> NotifyReport:
        """Send a test message to every configured channel, ignoring the routing toggles."""
        title, body = build_test_message(config)
        n = config.notifications
        return self._dispatch(build_channels(n), title, body, n)

    def _dispatch(
        self, channels: list[Channel], title: str, body: str, n: NotificationsConfig
    ) -> NotifyReport:
        if not channels:
            return NotifyReport(sent=False, channels=0, reason="no_channels")
        secrets = _secrets(n)
        results = [self._send_one(ch, title, body, secrets) for ch in channels]
        sent = all(r.ok for r in results)
        return NotifyReport(
            sent=sent,
            channels=len(results),
            error=None if sent else "delivery failed",
            results=results,
        )

    def _send_one(
        self, channel: Channel, title: str, body: str, secrets: list[str]
    ) -> ChannelResult:
        engine = self._apprise_factory()
        if not engine.add(channel.url):
            return ChannelResult(channel=channel.name, ok=False, error="invalid URL")
        try:
            with _captured_apprise_logs() as captured:
                ok = bool(engine.notify(title=title, body=body))
        except Exception as exc:  # a broken channel must not stop the others
            return ChannelResult(channel=channel.name, ok=False, error=_scrub(str(exc), secrets))
        if ok:
            return ChannelResult(channel=channel.name, ok=True)
        reason = captured.messages[-1] if captured.messages else None
        return ChannelResult(
            channel=channel.name,
            ok=False,
            error=_scrub(reason, secrets) if reason else None,
        )
