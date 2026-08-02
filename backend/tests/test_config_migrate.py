"""0.9 -> 1.0 config migration.

Anchored on tests/fixtures/config-0.9.yaml — the full set of keys config.example.yaml
shipped at tag v0.9.0, filled in — so a key the mapping forgets fails here instead of
quietly disappearing from a real user's config.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app import config_migrate
from app.config import REDACTED, Config, load_config, save_config

FIXTURE = Path(__file__).parent / "fixtures" / "config-0.9.yaml"
BAK = "config.yaml" + config_migrate.BACKUP_SUFFIX


def _write(tmp_path: Path, **overrides) -> Path:
    """Drop the 0.9 fixture into tmp_path, deep-merging any section overrides."""
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    for section, values in overrides.items():
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(raw[section].get(key), dict):
                raw[section][key].update(value)
            else:
                raw[section][key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


# --- detection ----------------------------------------------------------------


def test_a_0_9_config_needs_migration():
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert config_migrate.needs_migration(raw) is True


def test_a_migrated_config_is_never_migrated_again():
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert config_migrate.needs_migration(config_migrate.migrate(raw)) is False


def test_an_empty_route_list_still_counts_as_migrated():
    # Key presence, not a non-empty list: someone who deletes their last route must not
    # have one conjured back on the next boot.
    assert config_migrate.needs_migration({"pve": {}, "routes": []}) is False


def test_a_config_without_the_old_sections_is_left_alone():
    assert config_migrate.needs_migration({"app": {}}) is False


# --- the happy path -----------------------------------------------------------


@pytest.fixture
def migrated(tmp_path: Path) -> Config:
    return load_config(_write(tmp_path))


def test_pve_becomes_a_device(migrated: Config):
    assert len(migrated.pves) == 1
    pve = migrated.pves[0]
    assert pve.id == "pve-lab"  # 0.9's node name makes the nicest id
    assert pve.host == "192.0.2.10"
    assert pve.verify_tls is True
    assert pve.api_token_id == "root@pam!joulenap"
    assert pve.api_token_secret == "test-pve-secret"
    # The single 0.9 storage_id is how this pve names that pbs.
    assert pve.storages == {"pbs": "pbs-store"}


def test_pbs_becomes_a_device(migrated: Config):
    assert len(migrated.pbss) == 1
    pbs = migrated.pbss[0]
    assert pbs.id == "pbs"  # from the host label "pbs.example.test"
    assert pbs.host == "pbs.example.test"
    assert pbs.datastore == "backup"
    assert pbs.fingerprint == "aa:bb:cc:dd:ee:ff"
    assert pbs.api_token_secret == "test-pbs-secret"
    assert pbs.managed_power is True and pbs.mac == "00:11:22:33:44:55"
    assert pbs.wol_broadcast_iface == "eth0"
    assert (pbs.wait_timeout, pbs.wol_retries, pbs.poweroff_task_wait) == (240, 3, 900)
    assert pbs.ssh_user == "backup"
    # The External watch timeouts were global in 0.9; they describe this box, so they move
    # onto the device even when external mode is off.
    assert pbs.external.first_task_wait == 1200 and pbs.external.idle_wait == 420


def test_backup_becomes_one_route(migrated: Config):
    assert len(migrated.routes) == 1
    route = migrated.routes[0]
    assert route.kind == "backup"
    assert route.name == "Backup" and route.color == config_migrate.BACKUP_ROUTE_COLOR
    assert route.enabled is True
    assert route.target == "pbs"
    assert [s.pve for s in route.sources] == ["pve-lab"]
    assert route.sources[0].guests.mode == "include"
    assert route.sources[0].guests.list == [104, 106, 202]
    assert route.schedule.time == "02:30"
    assert route.schedule.days == [True] * 5 + [False, False]  # Mon..Fri
    assert route.retention.keep_last == 2 and route.retention.keep_yearly == 1
    assert route.options.mode == "suspend"
    assert route.options.bwlimit == 5000
    assert route.options.min_free_percent == 15
    assert route.options.gc is False  # maintenance.gc.enabled was off
    assert route.options.verify_after is True  # maintenance.verify.after_backup was on


def test_the_rest_of_the_config_is_untouched(migrated: Config):
    assert migrated.app.language == "it" and migrated.app.port == 8081
    assert migrated.notifications.telegram.bot_token == "test-bot-token"
    assert migrated.notifications.custom_urls == ["gotify://example.test/token"]
    # history retention is the one part of maintenance: with no route equivalent; gc and
    # verify became per-route options and a Verify route.
    assert migrated.maintenance.history.retention_days == 21


# --- the parachute ------------------------------------------------------------


def test_the_original_is_backed_up_before_the_rewrite(tmp_path: Path):
    path = _write(tmp_path)
    original = path.read_text(encoding="utf-8")
    load_config(path)
    assert (tmp_path / BAK).read_text(encoding="utf-8") == original
    assert path.read_text(encoding="utf-8") != original  # rewritten in the new shape


def test_the_backup_is_written_once(tmp_path: Path):
    # The first migration wins: a later one must not overwrite the true 0.9 original.
    path = _write(tmp_path)
    load_config(path)
    (tmp_path / BAK).write_text("the real original\n", encoding="utf-8")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))  # force a second migration
    del raw["routes"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    load_config(path)

    assert (tmp_path / BAK).read_text(encoding="utf-8") == "the real original\n"


def test_migration_is_idempotent(tmp_path: Path):
    path = _write(tmp_path)
    first = load_config(path)
    assert load_config(path) == first


def test_a_config_that_cannot_be_converted_is_left_alone(tmp_path: Path, monkeypatch):
    # Whatever goes wrong, the app still boots — on the untouched 0.9 config (BE-B1).
    path = _write(tmp_path)
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(config_migrate, "migrate", lambda raw: {**raw, "routes": "nonsense"})
    cfg = load_config(path)
    # Nothing was converted and nothing was written; the 0.9 sections on disk are simply
    # dropped at load, so the app boots on defaults rather than failing to start.
    assert cfg.routes == []
    assert path.read_text(encoding="utf-8") == original
    assert not (tmp_path / BAK).exists()


def test_an_unwritable_config_still_boots(tmp_path: Path, monkeypatch):
    path = _write(tmp_path)
    monkeypatch.setattr(
        "app.config.save_config", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
    )
    cfg = load_config(path)  # must not raise
    assert len(cfg.routes) == 1


# --- partially configured 0.9 installs ----------------------------------------


def test_an_unconfigured_0_9_install_migrates_to_nothing(tmp_path: Path):
    # Installed 0.9, never ran the wizard: there is nothing to describe, and inventing a
    # device with a blank host would fail validation and brick the boot.
    path = _write(tmp_path, pve={"host": ""}, pbs={"host": ""})
    cfg = load_config(path)
    assert cfg.pves == [] and cfg.pbss == [] and cfg.routes == []


def test_a_pve_without_a_storage_keeps_its_device_but_gets_no_route(tmp_path: Path):
    # A backup route with no storage mapping would not validate — and 0.9 could not have
    # backed up either. Keep the device, skip the route.
    path = _write(tmp_path, pve={"storage_id": ""})
    cfg = load_config(path)
    assert [p.id for p in cfg.pves] == ["pve-lab"] and cfg.routes == []


def test_a_pbs_without_a_mac_becomes_unmanaged(tmp_path: Path):
    path = _write(tmp_path, pbs={"mac": ""})
    cfg = load_config(path)
    assert cfg.pbss[0].managed_power is False
    assert len(cfg.routes) == 1  # a backup route onto an always-on PBS is still fine


def test_external_mode_without_a_mac_gets_no_route(tmp_path: Path):
    # An External route needs a PBS Joulenap can wake; without a MAC there is nothing to
    # wake, so the device migrates and the route does not.
    path = _write(tmp_path, pbs={"mac": ""}, backup={"external": {"enabled": True}})
    cfg = load_config(path)
    assert cfg.pbss[0].managed_power is False and cfg.routes == []


def test_ids_fall_back_when_the_host_is_an_ip(tmp_path: Path):
    path = _write(tmp_path, pve={"node": ""}, pbs={"host": "192.0.2.20"})
    cfg = load_config(path)
    assert cfg.pves[0].id == "pve" and cfg.pbss[0].id == "pbs"


def test_a_shared_hostname_does_not_collide(tmp_path: Path):
    path = _write(tmp_path, pve={"node": "", "host": "box.lan"}, pbs={"host": "box.lan"})
    cfg = load_config(path)
    assert cfg.pves[0].id == "box" and cfg.pbss[0].id == "box-pbs"
    assert cfg.pves[0].storages == {"box-pbs": "pbs-store"}


# --- the other route kinds ----------------------------------------------------


def test_external_mode_becomes_an_external_route(tmp_path: Path):
    path = _write(tmp_path, backup={"external": {"enabled": True}})
    cfg = load_config(path)
    route = cfg.routes[0]
    assert route.kind == "external" and route.name == "Backup"
    assert route.sources == [] and route.target == "pbs"
    assert route.schedule.time == "02:30"
    # In external mode PVE/PBS run their own jobs; Joulenap starts no GC of its own.
    assert route.options.gc is False


def test_scheduled_verify_becomes_a_second_route(tmp_path: Path):
    path = _write(tmp_path, maintenance={"verify": {"enabled": True}})
    cfg = load_config(path)
    assert [r.kind for r in cfg.routes] == ["backup", "verify"]
    verify = cfg.routes[1]
    assert verify.id == "verify" and verify.name == "Verify"
    assert verify.color == config_migrate.VERIFY_ROUTE_COLOR
    assert verify.sources == [] and verify.target == "pbs"
    assert verify.options.reverify_days == 45  # would otherwise be silently lost
    assert verify.options.gc is False
    # "0 3 1 * *" pins a day of month, so it is preserved verbatim rather than approximated.
    assert verify.schedule.cron == "0 3 1 * *"


def test_a_disabled_backup_job_migrates_to_a_disabled_route(tmp_path: Path):
    path = _write(tmp_path, backup={"enabled": False})
    assert load_config(path).routes[0].enabled is False


# --- guest modes --------------------------------------------------------------


def test_exclude_mode_widens_to_all(tmp_path: Path, caplog):
    # 1.0 has no exclude mode and inverting the list needs a live guest list we don't have
    # at load time. Widening never drops a guest from a backup; narrowing could.
    path = _write(tmp_path, backup={"guests": {"mode": "exclude", "list": [999]}})
    with caplog.at_level("WARNING"):
        cfg = load_config(path)
    guests = cfg.routes[0].sources[0].guests
    assert guests.mode == "all" and guests.list == []
    assert "exclude" in caplog.text


def test_all_mode_maps_straight_through(tmp_path: Path):
    path = _write(tmp_path, backup={"guests": {"mode": "all", "list": []}})
    assert load_config(path).routes[0].sources[0].guests.mode == "all"


# --- cron ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "time", "days"),
    [
        ("0 4 * * *", "04:00", [True] * 7),
        ("30 2 * * 1,2,3,4,5", "02:30", [True] * 5 + [False, False]),
        ("0 23 * * 0", "23:00", [False] * 6 + [True]),  # cron Sunday is 0
        ("0 23 * * 7", "23:00", [False] * 6 + [True]),  # ...and 7
        ("5 0 * * 6", "00:05", [False] * 5 + [True, False]),  # Saturday
    ],
)
def test_a_plain_cron_becomes_time_and_days(cron: str, time: str, days: list[bool]):
    assert config_migrate.parse_cron(cron) == {"time": time, "days": days}


@pytest.mark.parametrize(
    "cron",
    [
        "0 3 1 * *",  # day of month
        "0 3 * 6 *",  # month
        "*/15 4 * * *",  # step in the minute field
        "0 4 * * 1-5",  # weekday range
        "0 4 * * mon,fri",  # weekday names
        "0 4 * *",  # not five fields
    ],
)
def test_a_cron_the_form_cannot_express_is_kept_verbatim(cron: str):
    assert config_migrate.parse_cron(cron) == {"cron": cron}


def test_an_empty_cron_falls_back_to_the_schedule_defaults():
    assert config_migrate.parse_cron("") == {}


def test_a_pinned_cron_skips_the_day_check():
    # days[] is unused when cron is set, so the "no day selected" guard must not fire.
    schedule = {"cron": "0 3 1 * *", "days": [False] * 7}
    cfg = Config.model_validate(
        {
            "pbss": [{"id": "pbs", "host": "192.0.2.20", "mac": "00:11:22:33:44:55"}],
            "routes": [{"id": "v", "kind": "verify", "target": "pbs", "schedule": schedule}],
        }
    )
    assert cfg.routes[0].schedule.cron == "0 3 1 * *"


def test_a_malformed_pinned_cron_is_rejected():
    with pytest.raises(ValueError, match="is not a 5-field crontab string"):
        Config.model_validate(
            {
                "pbss": [{"id": "pbs", "host": "192.0.2.20", "mac": "00:11:22:33:44:55"}],
                "routes": [
                    {"id": "v", "kind": "verify", "target": "pbs", "schedule": {"cron": "nope"}}
                ],
            }
        )


# --- secrets ------------------------------------------------------------------


def test_secrets_survive_the_migration_to_disk(tmp_path: Path):
    # The migrated file is written with real secrets, not the redaction sentinel.
    path = _write(tmp_path)
    load_config(path)
    text = path.read_text(encoding="utf-8")
    assert "test-pve-secret" in text and REDACTED not in text
    reloaded = load_config(path)
    assert reloaded.pves[0].api_token_secret == "test-pve-secret"
    assert reloaded.pbss[0].api_token_secret == "test-pbs-secret"
    assert reloaded.app.auth.password_hash == "$2b$12$abcdefghijklmnopqrstuv"


def test_the_migrated_file_reloads_unchanged(tmp_path: Path):
    path = _write(tmp_path)
    cfg = load_config(path)
    again = tmp_path / "copy.yaml"
    save_config(cfg, again)
    assert load_config(again) == cfg
