"""Connector exception hierarchy."""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for all connector failures."""


class WolError(ConnectorError):
    """Failed to build or send the Wake-on-LAN magic packet."""


class PowerError(ConnectorError):
    """SSH connection or poweroff command failed."""


class ApiError(ConnectorError):
    """A PVE/PBS API call failed.

    ``status`` is the HTTP status code when the server responded; ``None`` for
    transport-level failures (connection refused, timeout, TLS).
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class TaskError(ConnectorError):
    """A PVE/PBS background task (vzdump, GC) finished with a non-OK status."""

    def __init__(self, message: str, exit_status: str | None = None):
        super().__init__(message)
        self.exit_status = exit_status


class TaskCancelled(ConnectorError):
    """The caller asked to stop waiting on a task (user-requested cancellation).

    Deliberately *not* a :class:`TaskError`: the remote task didn't fail, we chose to stop
    waiting for it — the two lead to different run outcomes (aborted vs failed).
    """
