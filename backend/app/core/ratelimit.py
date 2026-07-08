"""In-memory per-IP login rate-limiting (JN-009).

A single-admin LAN app doesn't need a store or a new dependency — a small dict guarded by a
lock is enough to blunt online brute force. After ``max_failures`` failed attempts from an
IP the address is locked for ``lockout_seconds``; a success (or the window expiring) clears it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        max_failures: int = 5,
        lockout_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ):
        self._max = max_failures
        self._lockout = lockout_seconds
        self._now = now
        self._lock = threading.Lock()
        # ip -> [failure_count, locked_until]
        self._state: dict[str, list[float]] = {}

    def locked_for(self, ip: str) -> float:
        """Seconds remaining in the lockout for ``ip`` (0.0 if not locked). Clears an
        expired entry as a side effect so the next attempt starts a fresh streak."""
        with self._lock:
            entry = self._state.get(ip)
            if not entry:
                return 0.0
            if entry[1] <= 0:
                return 0.0
            remaining = entry[1] - self._now()
            if remaining <= 0:
                del self._state[ip]
                return 0.0
            return remaining

    def record_failure(self, ip: str) -> None:
        with self._lock:
            entry = self._state.setdefault(ip, [0.0, 0.0])
            entry[0] += 1
            if entry[0] >= self._max:
                entry[1] = self._now() + self._lockout

    def reset(self, ip: str) -> None:
        with self._lock:
            self._state.pop(ip, None)
