"""In-memory holder for the live config, with thread-safe reload and persistence.

One instance lives on ``app.state.config_store``; routers read and mutate config
through it so there's a single source of truth that stays in sync with ``config.yaml``.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from .. import paths
from ..config import Config, load_config, restrict_secret_file, save_config

log = logging.getLogger("joulenap.config")

_PLACEHOLDER_SECRETS = {"", "CHANGE_ME"}


class ConfigStore:
    def __init__(self, config: Config, path: Path):
        self._config = config
        self._path = path
        self._lock = threading.RLock()

    @classmethod
    def load_or_create(cls, path: Path | None = None) -> ConfigStore:
        """Load config.yaml (creating it from the example if absent) and harden it.

        On first run we seed a random ``secret_key`` so session cookies aren't signed
        with the committed placeholder. Persistence is best-effort: a read-only config
        mount logs a warning and keeps the generated key in memory only.
        """
        target = path or paths.config_path()
        if not target.exists():
            example = paths.config_example_path()
            log.warning("config.yaml not found at %s — creating from %s", target, example)
            shutil.copyfile(example, target)
            restrict_secret_file(target)  # owner-only from the start (secrets land here later)

        config = load_config(target)
        store = cls(config, target)

        if config.app.secret_key in _PLACEHOLDER_SECRETS:
            config.app.secret_key = secrets.token_hex(32)
            log.info("Generated a new app.secret_key for session signing")
            store._persist_best_effort()
        return store

    @property
    def config(self) -> Config:
        return self._config

    @property
    def path(self) -> Path:
        return self._path

    def reload(self) -> Config:
        """Re-read config.yaml from disk (e.g. after an external edit)."""
        with self._lock:
            self._config = load_config(self._path)
            return self._config

    def update(self, mutator: Callable[[Config], None]) -> Config:
        """Mutate the config under lock and persist it. Raises if the write fails."""
        with self._lock:
            mutator(self._config)
            save_config(self._config, self._path)
            return self._config

    def replace(self, config: Config) -> Config:
        """Swap in a fully-built config and persist it."""
        with self._lock:
            self._config = config
            save_config(self._config, self._path)
            return self._config

    def _persist_best_effort(self) -> None:
        try:
            save_config(self._config, self._path)
        except OSError as exc:
            log.warning("Could not persist config (%s); continuing with in-memory values", exc)
