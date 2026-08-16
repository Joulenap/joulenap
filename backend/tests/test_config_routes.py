"""The v1.0 route model: pves[] / pbss[] / routes[] and their cross-validation.

Every error these assert is one a user can act on from the UI or config.yaml, so the tests
check the message names the offending id, not just that something raised.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config as cfgmod
from app import paths
from app.config import Config, load_config, redacted_dict, restore_secrets, save_config

EXAMPLE = paths.config_example_path()


def _route(**over) -> dict:
    base = {
        "id": "nightly",
        "kind": "backup",
        "target": "pbs-01",
        "sources": [{"pve": "pve-01"}],
    }
    return {**base, **over}


def _cfg(**over) -> Config:
    """A valid minimal two-device, one-backup-route config; override any top-level list."""
    base = {
        "pves": [{"id": "pve-01", "host": "192.0.2.10", "storages": {"pbs-01": "pbs"}}],
        "pbss": [{"id": "pbs-01", "host": "192.0.2.20", "mac": "00:11:22:33:44:55"}],
        "routes": [_route()],
    }
    return Config.model_validate({**base, **over})


# --- the shipped example ------------------------------------------------------


def test_example_ships_no_devices_or_routes():
    # config_store copies the example on first run, so anything listed here would show up
    # as a phantom device/route on a brand-new install. The shape lives in comments.
    cfg = load_config(EXAMPLE)
    assert cfg.pves == [] and cfg.pbss == [] and cfg.routes == []


# --- defaults -----------------------------------------------------------------


def test_route_defaults():
    route = _cfg().routes[0]
    assert route.enabled is True and route.notify is True
    assert route.schedule.time == "04:00" and route.schedule.days == [True] * 7
    assert route.retention.keep_daily == 7
    assert route.options.mode == "snapshot" and route.options.gc is True
    # Sync knobs default to PBS's own defaults, so a 1.0 YAML without them changes nothing.
    assert route.options.transfer_last == 0 and route.options.remove_vanished is False
    assert route.sources[0].guests.mode == "all"


def test_pbs_defaults():
    pbs = _cfg().pbss[0]
    assert pbs.port == 8007 and pbs.managed_power is True
    assert pbs.external.first_task_wait == 900 and pbs.external.idle_wait == 300


# --- kind coherence -----------------------------------------------------------


def test_backup_route_needs_a_source():
    with pytest.raises(ValidationError, match="needs at least one source pve"):
        _cfg(routes=[_route(sources=[])])


def test_backup_route_rejects_source_pbs():
    with pytest.raises(ValidationError, match="source_pbs belongs to sync routes only"):
        _cfg(routes=[_route(source_pbs="pbs-02")])


def test_sync_route_is_valid_between_two_pbs():
    cfg = _cfg(
        pbss=[
            {"id": "pbs-01", "host": "192.0.2.20", "mac": "00:11:22:33:44:55"},
            {"id": "pbs-02", "host": "192.0.2.21", "mac": "00:11:22:33:44:66"},
        ],
        routes=[
            _route(id="offsite", kind="sync", sources=[], source_pbs="pbs-01", target="pbs-02")
        ],
    )
    assert cfg.routes[0].sync_direction == "pull"


def test_sync_route_options_are_validated():
    pbss = [
        {"id": "pbs-01", "host": "192.0.2.20", "mac": "00:11:22:33:44:55"},
        {"id": "pbs-02", "host": "192.0.2.21", "mac": "00:11:22:33:44:66"},
    ]
    sync = dict(id="offsite", kind="sync", sources=[], source_pbs="pbs-01", target="pbs-02")
    options = {"transfer_last": 3, "remove_vanished": True}
    cfg = _cfg(pbss=pbss, routes=[_route(**sync, options=options)])
    assert cfg.routes[0].options.transfer_last == 3
    assert cfg.routes[0].options.remove_vanished is True
    with pytest.raises(ValidationError):
        _cfg(pbss=pbss, routes=[_route(**sync, options={"transfer_last": -1})])


def test_sync_route_rejects_pve_sources():
    with pytest.raises(ValidationError, match="a sync route has no pve sources"):
        _cfg(routes=[_route(kind="sync", source_pbs="pbs-01")])


def test_sync_route_needs_a_source_pbs():
    with pytest.raises(ValidationError, match="a sync route needs source_pbs"):
        _cfg(routes=[_route(kind="sync", sources=[])])


def test_sync_route_rejects_same_source_and_target():
    with pytest.raises(ValidationError, match="the same pbs"):
        _cfg(routes=[_route(kind="sync", sources=[], source_pbs="pbs-01", target="pbs-01")])


@pytest.mark.parametrize("kind", ["external", "verify"])
def test_sourceless_kinds_reject_sources(kind: str):
    with pytest.raises(ValidationError, match=f"a {kind} route takes no sources"):
        _cfg(routes=[_route(kind=kind)])


@pytest.mark.parametrize("kind", ["external", "verify"])
def test_sourceless_kinds_are_valid_with_only_a_target(kind: str):
    cfg = _cfg(routes=[_route(kind=kind, sources=[])])
    assert cfg.routes[0].kind == kind


# --- references ---------------------------------------------------------------


def test_unknown_target_names_the_known_ids():
    with pytest.raises(ValidationError, match="target 'nope' is not a known pbs id"):
        _cfg(routes=[_route(target="nope")])


def test_unknown_source_pve_is_rejected():
    with pytest.raises(ValidationError, match="source 'ghost' is not a known pve id"):
        _cfg(routes=[_route(sources=[{"pve": "ghost"}])])


def test_unknown_source_pbs_is_rejected():
    with pytest.raises(ValidationError, match="source_pbs 'ghost' is not a known pbs id"):
        _cfg(routes=[_route(kind="sync", sources=[], source_pbs="ghost")])


@pytest.mark.parametrize(
    ("section", "entries"),
    [
        ("pves", [{"id": "dup", "storages": {"pbs-01": "pbs"}}] * 2),
        ("pbss", [{"id": "dup"}] * 2),
    ],
)
def test_duplicate_device_ids_are_rejected(section: str, entries: list[dict]):
    with pytest.raises(ValidationError, match=f"{section}: duplicate id 'dup'"):
        _cfg(**{section: entries, "routes": []})


def test_duplicate_route_ids_are_rejected():
    with pytest.raises(ValidationError, match="routes: duplicate id 'nightly'"):
        _cfg(routes=[_route(), _route()])


# --- storage mapping ----------------------------------------------------------


def test_backup_route_needs_a_storage_mapping_from_every_source():
    with pytest.raises(ValidationError, match="no storage mapping for pbs 'pbs-01'"):
        _cfg(pves=[{"id": "pve-01", "host": "192.0.2.10", "storages": {}}])


def test_storage_mapping_is_per_pve():
    # Two PVEs backing up to the same PBS, each naming that storage differently.
    cfg = _cfg(
        pves=[
            {"id": "pve-01", "host": "192.0.2.10", "storages": {"pbs-01": "pbs"}},
            {"id": "pve-02", "host": "192.0.2.11", "storages": {"pbs-01": "backup-store"}},
        ],
        routes=[_route(sources=[{"pve": "pve-01"}, {"pve": "pve-02"}])],
    )
    assert [p.storages["pbs-01"] for p in cfg.pves] == ["pbs", "backup-store"]


def test_sourceless_route_needs_no_storage_mapping():
    cfg = _cfg(
        pves=[{"id": "pve-01", "host": "192.0.2.10", "storages": {}}],
        routes=[_route(kind="verify", sources=[])],
    )
    assert cfg.pves[0].storages == {}


# --- per-source guests --------------------------------------------------------


def test_guests_are_selected_per_source():
    # The same vmid on two PVEs is two different guests; a flat list could not say which.
    cfg = _cfg(
        pves=[
            {"id": "pve-01", "host": "192.0.2.10", "storages": {"pbs-01": "pbs"}},
            {"id": "pve-02", "host": "192.0.2.11", "storages": {"pbs-01": "pbs"}},
        ],
        routes=[
            _route(
                sources=[
                    {"pve": "pve-01", "guests": {"mode": "include", "list": [100, 101]}},
                    {"pve": "pve-02", "guests": {"mode": "include", "list": [100]}},
                ]
            )
        ],
    )
    first, second = cfg.routes[0].sources
    assert first.guests.list == [100, 101] and second.guests.list == [100]


def test_exclude_guest_mode_is_gone():
    with pytest.raises(ValidationError):
        _cfg(routes=[_route(sources=[{"pve": "pve-01", "guests": {"mode": "exclude"}}])])


# --- managed_power ------------------------------------------------------------


def test_managed_power_requires_wake_and_poweroff_fields():
    with pytest.raises(ValidationError, match="pbs 'pbs-01': managed_power is on, so mac"):
        _cfg(pbss=[{"id": "pbs-01", "host": "192.0.2.20"}])


def test_unmanaged_pbs_needs_no_mac():
    cfg = _cfg(pbss=[{"id": "pbs-01", "host": "192.0.2.20", "managed_power": False}])
    assert cfg.pbss[0].managed_power is False and cfg.pbss[0].mac == ""


def test_half_filled_device_stays_saveable():
    # The wizard writes a device before every field is known; only a device with a host
    # is held to the managed_power contract.
    cfg = _cfg(pbss=[{"id": "pbs-01"}])
    assert cfg.pbss[0].host == ""


def test_external_route_onto_an_unmanaged_pbs_is_rejected():
    # Nothing to wake or power off, and PVE/PBS already own those schedules.
    with pytest.raises(ValidationError, match="managed_power: false"):
        _cfg(
            pbss=[{"id": "pbs-01", "host": "192.0.2.20", "managed_power": False}],
            routes=[_route(kind="external", sources=[])],
        )


def test_verify_route_onto_an_unmanaged_pbs_is_allowed():
    # Verify runs over the PBS API; an always-on box just never needs waking.
    cfg = _cfg(
        pbss=[{"id": "pbs-01", "host": "192.0.2.20", "managed_power": False}],
        routes=[_route(kind="verify", sources=[])],
    )
    assert cfg.routes[0].kind == "verify"


# --- schedule -----------------------------------------------------------------


def test_schedule_needs_exactly_seven_days():
    with pytest.raises(ValidationError):
        _cfg(routes=[_route(schedule={"days": [True] * 6})])


def test_schedule_with_no_day_selected_is_rejected():
    with pytest.raises(ValidationError, match="selects no day"):
        _cfg(routes=[_route(schedule={"days": [False] * 7})])


@pytest.mark.parametrize("time", ["24:00", "4:00", "04:60", "morning"])
def test_schedule_time_must_be_hh_mm(time: str):
    with pytest.raises(ValidationError):
        _cfg(routes=[_route(schedule={"time": time})])


@pytest.mark.parametrize("color", ["f5a524", "#f5a52", "#nothex", "red"])
def test_route_color_must_be_a_hex_string(color: str):
    with pytest.raises(ValidationError):
        _cfg(routes=[_route(color=color)])


@pytest.mark.parametrize("bad_id", ["", "-leading", "Upper", "has space"])
def test_ids_are_slugs(bad_id: str):
    with pytest.raises(ValidationError):
        _cfg(routes=[_route(id=bad_id)])


# --- secrets ------------------------------------------------------------------


def test_device_tokens_are_redacted():
    cfg = _cfg()
    cfg.pves[0].api_token_secret = "pve-tok"
    cfg.pbss[0].api_token_secret = "pbs-tok"
    red = redacted_dict(cfg)
    assert red["pves"][0]["api_token_secret"] == cfgmod.REDACTED
    assert red["pbss"][0]["api_token_secret"] == cfgmod.REDACTED


def test_redacted_device_token_is_restored_on_put():
    cfg = _cfg()
    cfg.pves[0].api_token_secret = "pve-tok"
    incoming = {"pves": [{"id": "pve-01", "api_token_secret": cfgmod.REDACTED}]}
    out = restore_secrets(incoming, cfg)
    assert out["pves"][0]["api_token_secret"] == "pve-tok"


# --- round trip ---------------------------------------------------------------


def test_routes_survive_save_and_load(tmp_path: Path):
    cfg = _cfg()
    out = tmp_path / "config.yaml"
    save_config(cfg, out)
    assert load_config(out) == cfg
