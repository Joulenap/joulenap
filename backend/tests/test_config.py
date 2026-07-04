"""Config loader/writer tests, anchored on the committed config.example.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config as cfgmod
from app import paths
from app.config import Config, load_config, redact, redacted_dict, restore_secrets, save_config

EXAMPLE = paths.config_example_path()


def test_example_config_loads_and_validates():
    cfg = load_config(EXAMPLE)
    assert cfg.app.port == 8080
    assert cfg.pve.port == 8006
    assert cfg.pbs.port == 8007
    assert cfg.backup.guests.mode == "all"
    assert cfg.backup.retention.keep_daily == 7
    # Example ships with notifications off (unconfigured, no leaked tokens).
    assert cfg.notifications.telegram.enabled is False


def test_defaults_when_empty(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("app: {}\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.app.auth.username == "admin"
    assert cfg.backup.enabled is True


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_unknown_key_rejected(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("app:\n  bogus_field: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(p)


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


def test_redaction_masks_secrets_keeps_empty():
    cfg = load_config(EXAMPLE)
    cfg.app.secret_key = "supersecret"
    cfg.pve.api_token_secret = "tok"
    cfg.notifications.custom_urls = ["tgram://a/b"]
    red = redacted_dict(cfg)
    assert red["app"]["secret_key"] == cfgmod.REDACTED
    assert red["pve"]["api_token_secret"] == cfgmod.REDACTED
    assert red["notifications"]["custom_urls"] == [cfgmod.REDACTED]
    # Empty secret stays empty so the UI can show "not set". The example ships this blank
    # (unconfigured), so redaction leaves it as-is rather than masking.
    assert red["pbs"]["api_token_secret"] == ""


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
    cfg.pve.api_token_secret = "tok"
    incoming = {"pve": {"api_token_secret": ""}}
    out = restore_secrets(incoming, cfg)
    assert out["pve"]["api_token_secret"] == ""


def test_empty_secret_not_masked():
    cfg = Config()
    cfg.pve.api_token_secret = ""
    red = redacted_dict(cfg)
    assert red["pve"]["api_token_secret"] == ""
