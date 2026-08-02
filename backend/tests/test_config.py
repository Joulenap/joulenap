"""Config loader/writer tests, anchored on the committed config.example.yaml."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config as cfgmod
from app import paths
from app.config import (
    Config,
    PbsDevice,
    PveDevice,
    load_config,
    redact,
    redacted_dict,
    restore_secrets,
    save_config,
)

EXAMPLE = paths.config_example_path()


def test_example_config_loads_and_validates():
    cfg = load_config(EXAMPLE)
    assert cfg.app.port == 8080
    assert cfg.app.scheduler_enabled is True
    # The example ships no devices and no routes: a fresh install lands in the wizard, and
    # anything listed here would appear as a phantom device (config_store copies it verbatim).
    assert (cfg.pves, cfg.pbss, cfg.routes) == ([], [], [])
    assert cfg.maintenance.history.retention_days == 14
    # Example ships with notifications off (unconfigured, no leaked tokens).
    assert cfg.notifications.telegram.enabled is False


def test_defaults_when_empty(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("app: {}\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.app.auth.username == "admin"
    assert cfg.app.scheduler_enabled is True


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_unknown_key_rejected(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("app:\n  bogus_field: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(p)


def test_legacy_sections_are_dropped_not_rejected(tmp_path: Path):
    # BE-C5, now for the whole 0.9 model: extra="forbid" would turn a leftover pve:/pbs:/
    # backup: section into a startup failure after a container pull, so the loader strips
    # them — and the next save writes the file without them. ``routes`` being present is what
    # marks the file as already migrated, so this is *not* the migration path.
    p = tmp_path / "config.yaml"
    p.write_text(
        "routes: []\n"
        "pve:\n  host: 192.0.2.10\n"
        "pbs:\n  host: 192.0.2.20\n"
        "backup:\n  guests:\n    mode: include\n    auto_include_new: true\n"
        "maintenance:\n  gc:\n    enabled: true\n  history:\n    retention_days: 21\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.maintenance.history.retention_days == 21  # the half that survived
    save_config(cfg, p)
    text = p.read_text(encoding="utf-8")
    assert "auto_include_new" not in text
    for key in ("pve:", "pbs:", "backup:"):
        assert key not in text


def test_roundtrip_save_load(tmp_path: Path):
    cfg = load_config(EXAMPLE)
    cfg.app.auth.password_hash = "$2b$12$abcdefghijklmnopqrstuv"
    out = tmp_path / "config.yaml"
    save_config(cfg, out)
    again = load_config(out)
    assert again.app.auth.password_hash == cfg.app.auth.password_hash
    assert again == cfg


def test_save_falls_back_to_in_place_when_rename_is_busy(tmp_path: Path, monkeypatch):
    # A single-file Docker bind mount can't be replaced by rename (EBUSY); save_config must
    # fall back to overwriting the file in place so config still persists in a container.
    import errno
    import os

    out = tmp_path / "config.yaml"
    out.write_text("app:\n  port: 1\n", encoding="utf-8")  # stand-in for the mounted file

    def busy_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(os, "replace", busy_replace)

    cfg = load_config(EXAMPLE)
    cfg.app.port = 9090
    save_config(cfg, out)  # must not raise

    assert load_config(out).app.port == 9090
    assert not (tmp_path / "config.yaml.tmp").exists()  # temp cleaned up


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only (Windows/NTFS has no 0600)")
def test_save_config_is_owner_only(tmp_path: Path, monkeypatch):
    # config.yaml holds plaintext secrets (tokens, secret_key, passwords), so it must not be
    # world-readable (BE-S2) — mirrors the SSH key's 0600.
    import errno
    import stat

    cfg = load_config(EXAMPLE)
    out = tmp_path / "config.yaml"

    # Atomic path (temp + rename): the fresh file lands owner-only.
    save_config(cfg, out)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600

    # In-place fallback (bind-mount EBUSY) re-tightens even a loosened existing file.
    os.chmod(out, 0o644)

    def busy_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(os, "replace", busy_replace)
    save_config(cfg, out)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_redaction_masks_secrets_keeps_empty():
    cfg = load_config(EXAMPLE)
    cfg.app.secret_key = "supersecret"
    cfg.pves = [PveDevice(id="pve-01", api_token_secret="tok")]
    cfg.pbss = [PbsDevice(id="pbs-01")]  # no secret set yet
    cfg.notifications.custom_urls = ["tgram://a/b"]
    red = redacted_dict(cfg)
    assert red["app"]["secret_key"] == cfgmod.REDACTED
    assert red["pves"][0]["api_token_secret"] == cfgmod.REDACTED
    assert red["notifications"]["custom_urls"] == [cfgmod.REDACTED]
    # Empty secret stays empty so the UI can show "not set".
    assert red["pbss"][0]["api_token_secret"] == ""


def test_redact_does_not_mutate_source():
    cfg = load_config(EXAMPLE)
    data = cfg.model_dump(mode="python")
    _ = redact(data)
    assert data["app"]["secret_key"] == "CHANGE_ME"  # untouched


def test_restore_custom_urls_all_sentinels_keeps_stored():
    cfg = Config()
    cfg.notifications.custom_urls = ["gotify://h/a", "gotify://h/b"]
    incoming = {"notifications": {"custom_urls": [cfgmod.REDACTED, cfgmod.REDACTED]}}
    out = restore_secrets(incoming, cfg)
    assert out["notifications"]["custom_urls"] == ["gotify://h/a", "gotify://h/b"]


def test_restore_custom_urls_all_real_replaces():
    cfg = Config()
    cfg.notifications.custom_urls = ["gotify://h/a"]
    incoming = {"notifications": {"custom_urls": ["ntfy://x/y", "ntfy://x/z"]}}
    out = restore_secrets(incoming, cfg)
    assert out["notifications"]["custom_urls"] == ["ntfy://x/y", "ntfy://x/z"]


def test_restore_custom_urls_empty_clears():
    cfg = Config()
    cfg.notifications.custom_urls = ["gotify://h/a"]
    incoming = {"notifications": {"custom_urls": []}}
    out = restore_secrets(incoming, cfg)
    assert out["notifications"]["custom_urls"] == []


def test_restore_custom_urls_mixed_raises():
    cfg = Config()
    cfg.notifications.custom_urls = ["gotify://h/a", "gotify://h/b"]
    incoming = {"notifications": {"custom_urls": [cfgmod.REDACTED, "ntfy://new"]}}
    with pytest.raises(cfgmod.RedactionError):
        restore_secrets(incoming, cfg)


def test_restore_secret_empty_clears_scalar():
    cfg = Config()
    cfg.pves = [PveDevice(id="pve-01", api_token_secret="tok")]
    incoming = {"pves": [{"id": "pve-01", "api_token_secret": ""}]}
    out = restore_secrets(incoming, cfg)
    assert out["pves"][0]["api_token_secret"] == ""


def test_empty_secret_not_masked():
    cfg = Config()
    cfg.pves = [PveDevice(id="pve-01")]
    red = redacted_dict(cfg)
    assert red["pves"][0]["api_token_secret"] == ""


def test_device_secrets_are_matched_by_id_not_position():
    # The client reorders (or drops) a device and echoes ***REDACTED*** for the secrets it
    # did not change. Matching positionally would hand pbs-02's stored token to pbs-01.
    cfg = Config()
    cfg.pbss = [
        PbsDevice(id="pbs-01", api_token_secret="first", managed_power=False),
        PbsDevice(id="pbs-02", api_token_secret="second", managed_power=False),
    ]
    incoming = {
        "pbss": [
            {"id": "pbs-02", "api_token_secret": cfgmod.REDACTED},
            {"id": "pbs-01", "api_token_secret": cfgmod.REDACTED},
        ]
    }
    out = restore_secrets(incoming, cfg)
    assert [d["api_token_secret"] for d in out["pbss"]] == ["second", "first"]


def test_a_new_device_keeps_the_secret_it_was_sent():
    # An id with no stored counterpart must not silently inherit another device's token.
    cfg = Config()
    cfg.pbss = [PbsDevice(id="pbs-01", api_token_secret="first", managed_power=False)]
    incoming = {"pbss": [{"id": "pbs-99", "api_token_secret": "brand-new"}]}
    out = restore_secrets(incoming, cfg)
    assert out["pbss"][0]["api_token_secret"] == "brand-new"


def test_renaming_a_device_id_rejects_its_unresolvable_placeholder():
    """The Advanced tab shows ``id: pbs-01`` with ``api_token_secret: ***REDACTED***``. Fix a
    typo in the id and save: nothing matches by id any more, so the placeholder used to
    resolve to "" — 200 OK, credential gone, nothing on screen to suggest it."""
    cfg = Config()
    cfg.pbss = [PbsDevice(id="pbs-01", api_token_secret="first", managed_power=False)]
    incoming = {"pbss": [{"id": "pbs01", "api_token_secret": cfgmod.REDACTED}]}
    with pytest.raises(cfgmod.RedactionError, match="api_token_secret"):
        restore_secrets(incoming, cfg)


def test_a_placeholder_with_nothing_stored_is_rejected():
    # Same guard one level up: restore_secrets_from({}) is what a *create* resolves against.
    with pytest.raises(cfgmod.RedactionError):
        cfgmod.restore_secrets_from({"api_token_secret": cfgmod.REDACTED}, {})


def test_an_empty_string_still_clears_a_secret():
    # The escape hatch the rejection points at: "" means clear, and must keep working.
    cfg = Config()
    cfg.pbss = [PbsDevice(id="pbs-01", api_token_secret="first", managed_power=False)]
    out = restore_secrets({"pbss": [{"id": "pbs-01", "api_token_secret": ""}]}, cfg)
    assert out["pbss"][0]["api_token_secret"] == ""


def test_session_defaults():
    s = Config().app.session
    assert s.https_only is False and s.max_age_days == 14


def test_api_key_is_redacted():
    from app.config import Config, redacted_dict
    cfg = Config()
    cfg.app.api_key = "super-secret-key"
    out = redacted_dict(cfg)
    assert out["app"]["api_key"] == "***REDACTED***"


def test_api_key_is_server_managed_on_put():
    # enforce_server_managed must pin the stored api_key, ignoring client input.
    from app.config import Config, enforce_server_managed
    current = Config()
    current.app.api_key = "stored-key"
    merged = {"app": {"api_key": "attacker-supplied", "language": "it"}}
    result = enforce_server_managed(merged, current)
    assert result["app"]["api_key"] == "stored-key"
    assert result["app"]["language"] == "it"  # non-secret fields still pass through
