"""Shared fixtures: isolate the DB and config onto a tmp path per test session."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make sibling test helpers (e.g. tests/fakes.py) importable even when the `app`
# package is installed into the venv (which keeps tests/ off sys.path otherwise).
sys.path.insert(0, str(Path(__file__).parent))

from app import paths


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    """Point the DB at a throwaway file and (re)initialise the schema."""
    from app.db import base

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(base, "_engine", None)
    monkeypatch.setattr(base, "_SessionLocal", None)
    base.init_db(db_file)
    yield db_file
    monkeypatch.setattr(base, "_engine", None)
    monkeypatch.setattr(base, "_SessionLocal", None)


@pytest.fixture
def temp_config(tmp_path: Path, monkeypatch):
    """Write a fresh config.yaml from the example and route paths.config_path() to it.

    The shipped example is intentionally *unconfigured* (blank host/token/mac) so a real
    first run drops into the setup wizard. Tests want a set-up starting state, so we fill
    in fake connection values here — RFC 5737 TEST-NET IPs and a placeholder MAC/token,
    never real infrastructure — decoupling the suite from the example's leak-free contents.
    """
    from app.config import load_config, save_config

    example = paths.config_example_path().read_text(encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(example, encoding="utf-8")
    monkeypatch.setenv("JOULENAP_CONFIG", str(cfg_file))
    monkeypatch.setenv("JOULENAP_DATA_DIR", str(tmp_path / "data"))

    cfg = load_config(cfg_file)
    cfg.pve.host = "192.0.2.10"
    cfg.pve.node = "pve"
    cfg.pve.api_token_id = "root@pam!joulenap"
    cfg.pve.api_token_secret = "test-pve-secret"
    cfg.pve.storage_id = "pbs"
    cfg.pbs.host = "192.0.2.20"
    cfg.pbs.datastore = "backup"
    cfg.pbs.fingerprint = "aa:bb:cc:dd:ee:ff"
    cfg.pbs.api_token_id = "root@pam!joulenap"
    cfg.pbs.api_token_secret = "test-pbs-secret"
    cfg.pbs.mac = "00:11:22:33:44:55"
    cfg.pbs.wol_broadcast_iface = "eth0"
    save_config(cfg, cfg_file)
    yield cfg_file
