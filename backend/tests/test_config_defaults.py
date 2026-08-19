"""What the *model* declares, not what the shipped example happens to say.

Every other test loads ``config.example.yaml`` through the ``temp_config`` fixture, and the
example spells most of these values out. That makes the pydantic defaults behind them dead
weight as far as the suite is concerned: flip ``update_check`` to ``True`` in config.py and
``test_disabled_by_default_never_calls_out`` still passes, because it reads the ``false``
from the file. These tests construct the models with no YAML anywhere, so the declared
default is the thing under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app import paths
from app.config import (
    Config,
    DiscordConfig,
    EmailConfig,
    NtfyConfig,
    PbsDevice,
    PveDevice,
    RouteOptions,
    TelegramConfig,
)

# --- the defaults that decide what the app does unasked -----------------------


def test_no_outbound_call_is_configured_by_default():
    # The README's promise: Joulenap makes no internet call unless the user opts in.
    assert Config().app.update_check is False


def test_pve_tls_verification_defaults_to_off():
    # Homelab Proxmox certificates are self-signed, so off is the deliberate default —
    # but it has to be the *declared* one, not one the example happens to repeat.
    assert PveDevice(id="pve-01").verify_tls is False


def test_a_pbs_pins_nothing_until_it_is_given_a_fingerprint():
    # PBS has no verify_tls flag: it is pinned by fingerprint or not verified at all
    # (see jobs/deps._connect_pbs). An empty default is what "not pinned yet" looks like.
    assert PbsDevice(id="pbs-01", managed_power=False).fingerprint == ""


def test_no_notification_channel_is_enabled_by_default():
    # An enabled channel with no credentials would be attempted on every run and reported
    # as a failure; worse, a pre-filled one would send somewhere the user never chose.
    assert TelegramConfig().enabled is False
    assert NtfyConfig().enabled is False
    assert EmailConfig().enabled is False
    assert DiscordConfig().enabled is False
    assert Config().notifications.custom_urls == []


def test_both_routing_toggles_default_to_on():
    # The inverse risk: a user who configures a channel expects to hear about runs.
    n = Config().notifications
    assert (n.on_success, n.on_failure) == (True, True)


def test_a_fresh_install_has_no_devices_no_routes_and_an_armed_scheduler():
    cfg = Config()
    assert (cfg.pves, cfg.pbss, cfg.routes) == ([], [], [])
    assert cfg.app.scheduler_enabled is True  # nothing to fire yet, but not pre-disabled
    assert cfg.app.api_key == ""  # the dashboard integration is off until asked for


def test_power_management_defaults_match_what_the_docs_promise():
    pbs = PbsDevice(id="pbs-01", mac="00:11:22:33:44:55", ssh_key_path="/k")
    # managed_power on is what makes this a *power-saving* backup tool; the three timings
    # below are what the wizard shows and INSTALL.md documents.
    assert pbs.managed_power is True
    assert (pbs.wait_timeout, pbs.wol_retries, pbs.poweroff_task_wait) == (180, 2, 600)
    assert pbs.port == 8007


def test_route_options_default_to_gc_on_and_verify_off():
    opts = RouteOptions()
    assert (opts.gc, opts.verify_after) == (True, False)
    assert opts.min_free_percent == 0  # the preflight guard is opt-in
    assert opts.bwlimit == 0  # unlimited
    assert opts.mode == "snapshot"


# --- the example file must not contradict the model ---------------------------

#: The example is documentation as much as config, so a couple of values are deliberately
#: illustrative rather than default. Everything else drifting is a bug in one of the two.
_ALLOWED_DIVERGENCE = {
    "notifications.email.from_addr",  # a sample address, so the form shows the shape
}


def _flatten(node, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in (node or {}).items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        elif not isinstance(value, list):
            out[path] = value
    return out


def test_the_example_never_contradicts_a_model_default():
    """A value spelled out in config.example.yaml and declared in config.py must agree.

    They are two copies of the same decision and only one of them is under test anywhere
    else, so this is what stops them drifting.
    """
    raw = yaml.safe_load(paths.config_example_path().read_text(encoding="utf-8")) or {}
    example = _flatten(raw)
    model = _flatten(Config().model_dump(mode="python"))

    drift = {
        key: (value, model[key])
        for key, value in example.items()
        if key in model and key not in _ALLOWED_DIVERGENCE and value != model[key]
    }
    assert drift == {}, f"example vs model default: {drift}"


def test_the_example_still_loads_and_is_unconfigured():
    # It is what a first run copies into place, so it has to parse *and* land the user in
    # the setup wizard rather than in a half-configured app.
    cfg = _load_example()
    assert (cfg.pves, cfg.pbss, cfg.routes) == ([], [], [])


def _load_example() -> Config:
    from app.config import load_config

    return load_config(Path(paths.config_example_path()))


@pytest.mark.parametrize("field", ["secret_key", "api_key"])
def test_the_example_ships_no_usable_secret(field: str):
    # A committed real key would be the same key on every install.
    assert getattr(_load_example().app, field) in {"", "CHANGE_ME"}
