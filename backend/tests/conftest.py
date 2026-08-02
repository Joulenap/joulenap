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


#: The set-up starting state every API test runs against: two PVEs and two PBS devices with
#: three routes over them — which is also the acceptance-criterion shape (3 routes, 2 PBS,
#: so three cron jobs arm). Addresses are RFC 5737 TEST-NET and the tokens/MACs are
#: placeholders; nothing here ever points at real infrastructure.
DEVICES = {
    "pves": [
        {
            "id": "pve-alpha",
            "host": "192.0.2.10",
            "api_token_id": "root@pam!joulenap",
            "api_token_secret": "test-pve-secret",
            "storages": {"pbs-01": "pbs", "pbs-02": "pbs-offsite"},
        },
        {
            "id": "pve-beta",
            "host": "192.0.2.11",
            "api_token_id": "root@pam!joulenap",
            "api_token_secret": "test-pve2-secret",
            "storages": {"pbs-01": "pbs"},
        },
    ],
    "pbss": [
        {
            "id": "pbs-01",
            "host": "192.0.2.20",
            "datastore": "backup",
            "fingerprint": "aa:bb:cc:dd:ee:ff",
            "api_token_id": "root@pam!joulenap",
            "api_token_secret": "test-pbs-secret",
            "mac": "00:11:22:33:44:55",
            "wol_broadcast_iface": "eth0",
        },
        {
            "id": "pbs-02",
            "host": "192.0.2.21",
            "datastore": "offsite",
            "api_token_id": "root@pam!joulenap",
            "api_token_secret": "test-pbs2-secret",
            "mac": "00:11:22:33:44:66",
        },
    ],
}

ROUTES = [
    {
        "id": "nightly",
        "name": "Nightly",
        "kind": "backup",
        "sources": [{"pve": "pve-alpha"}, {"pve": "pve-beta"}],
        "target": "pbs-01",
        "schedule": {"time": "02:00"},
    },
    {
        "id": "lab",
        "name": "Lab",
        "kind": "backup",
        "sources": [{"pve": "pve-alpha", "guests": {"mode": "include", "list": [100, 101]}}],
        "target": "pbs-02",
        "schedule": {"time": "04:00", "days": [False] * 5 + [True, False]},
    },
    {
        "id": "offsite",
        "name": "Offsite sync",
        "kind": "sync",
        "source_pbs": "pbs-01",
        "target": "pbs-02",
        "schedule": {"time": "05:00"},
    },
]


@pytest.fixture
def temp_config(tmp_path: Path, monkeypatch):
    """Write a fresh config.yaml from the example and route paths.config_path() to it.

    The shipped example is intentionally *unconfigured* (no devices, no routes) so a real
    first run drops into the setup wizard. Tests want a set-up starting state, so the
    devices and routes above are filled in here — decoupling the suite from the example's
    leak-free contents.
    """
    from app.config import load_config, save_config

    example = paths.config_example_path().read_text(encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(example, encoding="utf-8")
    monkeypatch.setenv("JOULENAP_CONFIG", str(cfg_file))
    monkeypatch.setenv("JOULENAP_DATA_DIR", str(tmp_path / "data"))

    cfg = load_config(cfg_file)
    save_config(with_devices(cfg), cfg_file)
    yield cfg_file


def with_devices(cfg):
    """Fill a config in place with the standard device/route fixture."""
    from app.config import PbsDevice, PveDevice, Route

    cfg.pves = [PveDevice.model_validate(d) for d in DEVICES["pves"]]
    cfg.pbss = [PbsDevice.model_validate(d) for d in DEVICES["pbss"]]
    cfg.routes = [Route.model_validate(r) for r in ROUTES]
    return cfg
