"""Shared httpx base for the PVE and PBS API clients.

Both speak the Proxmox ``/api2/json`` dialect: token auth via an ``Authorization``
header, form-encoded POST bodies, and a ``{"data": ...}`` response envelope. They
differ only in the token header format and their endpoints, so that lives in the
subclasses (``pve``/``pbs``); everything common is here.
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

from .errors import ApiError


class ProxmoxApiClient:
    def __init__(
        self,
        base_url: str,
        auth_header: str,
        verify: bool | ssl.SSLContext = False,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            verify=verify,
            timeout=timeout,
            headers={"Authorization": auth_header},
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Call the API and return the unwrapped ``data`` payload.

        Raises :class:`ApiError` on transport failures (status ``None``) or any HTTP
        4xx/5xx (status set to the response code).
        """
        try:
            resp = self._client.request(method, path, params=params, data=data)
        except httpx.HTTPError as exc:
            raise ApiError(f"{method} {path} failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ApiError(
                f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise ApiError(f"{method} {path}: non-JSON response") from exc

        return body.get("data") if isinstance(body, dict) else body

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ProxmoxApiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
