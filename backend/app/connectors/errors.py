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


class TokenExistsError(ConnectorError):
    """An API token of that name already exists, and replacing it was not authorised.

    Deliberately *not* an :class:`ApiError`: the server did nothing wrong: we refused to act.
    A token's secret is only revealed when it is created, so the only way to end up with a
    usable one under a name already in use is to delete and recreate it — which silently
    invalidates the secret every *other* consumer of that token holds, most obviously the PBS
    storage entry on a Proxmox host. The caller has to say it means to do that.
    """


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
