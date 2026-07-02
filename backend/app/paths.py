"""Filesystem locations, resolved from env with container-friendly defaults.

In the Docker image the working dir is ``/app`` with ``config.yaml`` and ``data/``
mounted in; for local dev we fall back to the repo root (one level above ``backend/``).
Override either with ``JOULENAP_CONFIG`` / ``JOULENAP_DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/app/paths.py -> repo root is two parents up from this file's package dir.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _first_existing(*candidates: Path) -> Path:
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


def config_path() -> Path:
    """Path to ``config.yaml`` (env override wins, else /app then repo root)."""
    env = os.getenv("JOULENAP_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return _first_existing(
        Path("/app/config.yaml"),
        _REPO_ROOT / "config.yaml",
    )


def config_example_path() -> Path:
    """Path to the committed ``config.example.yaml`` (used as a fallback/template)."""
    return _first_existing(
        Path("/app/config.example.yaml"),
        _REPO_ROOT / "config.example.yaml",
    )


def data_dir() -> Path:
    """Directory for SQLite history, logs and the generated SSH key. Created if missing."""
    env = os.getenv("JOULENAP_DATA_DIR")
    base = Path(env).expanduser().resolve() if env else _first_existing(
        Path("/app/data"),
        _REPO_ROOT / "data",
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    """Path to the SQLite database file under the data dir."""
    return data_dir() / "joulenap.db"
