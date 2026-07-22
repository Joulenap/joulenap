"""Configuration schema, loader and writer.

The entire app is config-driven: ``config.yaml`` holds every setting and
all secrets. We validate it with pydantic so a malformed file fails clearly at startup,
and we can write it back atomically when the UI applies changes or the wizard saves.
"""

from __future__ import annotations

import errno
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, List, Literal  # noqa: UP035  (List is intentional, see below)

import yaml
from pydantic import BaseModel, ConfigDict, Field

from . import paths

# Field names whose values are secrets and must be masked before leaving the backend
# (e.g. GET /api/config). Matched by key name anywhere in the config tree.
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "secret_key",
        "api_key",
        "password_hash",
        "api_token_secret",
        "bot_token",
        "smtp_password",
        "webhook_url",
        "custom_urls",
    }
)

REDACTED = "***REDACTED***"


class _Base(BaseModel):
    # Reject unknown keys so typos in config.yaml surface as clear validation errors.
    model_config = ConfigDict(extra="forbid")


# --- app ---------------------------------------------------------------------


class AuthConfig(_Base):
    username: str = "admin"
    # Stored hashed (bcrypt). Empty => first-run registration via the UI.
    password_hash: str = ""


class SessionConfig(_Base):
    # Enable when Joulenap is served over HTTPS (or behind a TLS-terminating proxy).
    https_only: bool = False
    max_age_days: int = Field(default=14, ge=1)


class AppConfig(_Base):
    language: str = "en"
    theme: Literal["dark", "light"] = "dark"
    port: int = Field(default=8080, ge=1, le=65535)
    # IANA timezone name (e.g. "Europe/Rome") the scheduler interprets cron times in.
    # Empty => fall back to the TZ env var, then UTC. An invalid name falls back to UTC
    # with a warning (see core/scheduler.resolve_timezone).
    timezone: str = ""
    secret_key: str = "CHANGE_ME"
    # Read-only integration key for GET /api/dashboard (empty => integration disabled).
    # Managed only via POST/DELETE /api/config/api-key; PUT /api/config never touches it.
    api_key: str = ""
    # Opt-in: let GET /api/update ask GitHub (once a day) whether a newer release exists.
    # Off by default — the app makes no outbound internet call unless the user asks for it.
    update_check: bool = False
    auth: AuthConfig = Field(default_factory=AuthConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)


# --- pve ---------------------------------------------------------------------


class PveConfig(_Base):
    host: str = ""
    port: int = Field(default=8006, ge=1, le=65535)
    node: str = ""
    verify_tls: bool = False
    api_token_id: str = ""
    api_token_secret: str = ""
    storage_id: str = ""


# --- pbs ---------------------------------------------------------------------


class PbsConfig(_Base):
    host: str = ""
    port: int = Field(default=8007, ge=1, le=65535)
    datastore: str = ""
    fingerprint: str = ""
    api_token_id: str = ""
    api_token_secret: str = ""
    mac: str = ""
    wol_broadcast_iface: str = ""
    wait_timeout: int = Field(default=180, ge=0)  # per wake attempt
    # Extra Wake-on-LAN re-sends if the PBS doesn't come up within wait_timeout.
    # Total wake attempts = wol_retries + 1.
    wol_retries: int = Field(default=2, ge=0)
    # Before powering off, wait up to this many seconds for any running PBS task to finish
    # so a clean shutdown never interrupts it (0 = power off immediately, no guard).
    poweroff_task_wait: int = Field(default=600, ge=0)
    ssh_user: str = "root"
    ssh_key_path: str = "/app/data/id_ed25519"


# --- backup ------------------------------------------------------------------


class GuestsConfig(_Base):
    mode: Literal["all", "include", "exclude"] = "all"
    auto_include_new: bool = True
    # Field name matches the YAML key. ``typing.List`` (not ``list[int]``) avoids the
    # field name shadowing the builtin during Python 3.14 deferred annotation eval.
    list: List[int] = Field(default_factory=list)  # noqa: UP006


class RetentionConfig(_Base):
    keep_last: int = Field(default=0, ge=0)
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    keep_yearly: int = Field(default=0, ge=0)


class BackupConfig(_Base):
    enabled: bool = True
    schedule: str = "0 4 * * *"
    mode: Literal["snapshot", "suspend", "stop"] = "snapshot"
    bwlimit: int = Field(default=0, ge=0)
    # Pre-flight guard: abort before vzdump if the PBS datastore has less than this
    # percentage free (0 = disabled). Avoids backing up onto a near-full datastore.
    min_free_percent: int = Field(default=0, ge=0, le=100)
    guests: GuestsConfig = Field(default_factory=GuestsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)


# --- maintenance -------------------------------------------------------------


class GcConfig(_Base):
    # Simple on/off: when enabled, garbage collection runs after each backup, while
    # the PBS is still awake, before power-off. GC has no schedule or power cycle of
    # its own — it only ever piggybacks on the backup cycle.
    enabled: bool = True


class VerifyConfig(_Base):
    # Quick verify after each backup cycle: re-read only this run's *new* snapshots while the
    # PBS is already awake (already-verified data is skipped, so the extra awake-time is small).
    after_backup: bool = False
    # Periodic verification on its own wake -> verify -> power-off cycle.
    enabled: bool = False  # scheduled verify on/off
    schedule: str = "0 3 1 * *"  # cron; default 03:00 on the 1st of each month
    # Re-verify snapshots whose last verification is older than this many days, so the
    # scheduled verify stays mostly incremental. 0 = re-verify everything every run.
    reverify_days: int = Field(default=30, ge=0)


class HistoryConfig(_Base):
    # Auto-prune run history + activity-log rows older than this many days so the
    # SQLite DB under data/ can't grow without bound on a small disk. The prune runs
    # daily (see core/scheduler.py). 0 = keep everything forever (no pruning).
    retention_days: int = Field(default=14, ge=0)


class MaintenanceConfig(_Base):
    gc: GcConfig = Field(default_factory=GcConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)


# --- notifications -----------------------------------------------------------


class TelegramConfig(_Base):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class NtfyConfig(_Base):
    enabled: bool = False
    url: str = ""
    topic: str = ""


class EmailConfig(_Base):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    to_addr: str = ""


class DiscordConfig(_Base):
    enabled: bool = False
    webhook_url: str = ""


class NotificationsConfig(_Base):
    on_success: bool = True
    on_failure: bool = True
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    custom_urls: list[str] = Field(default_factory=list)


# --- root --------------------------------------------------------------------


class Config(_Base):
    app: AppConfig = Field(default_factory=AppConfig)
    pve: PveConfig = Field(default_factory=PveConfig)
    pbs: PbsConfig = Field(default_factory=PbsConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)


# --- load / save / redact ----------------------------------------------------


def load_config(path: Path | None = None) -> Config:
    """Read and validate ``config.yaml``. Raises with a clear message if missing/invalid."""
    p = path or paths.config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"Config file not found at {p}. Copy config.example.yaml to config.yaml."
        )
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config at {p} must be a YAML mapping, got {type(raw).__name__}.")
    return Config.model_validate(raw)


def restrict_secret_file(path: Path) -> None:
    """Best-effort ``chmod 0600`` so config.yaml's plaintext secrets (API tokens, secret_key,
    SMTP/bot passwords) aren't world-readable — matching the SSH key's perms. Silently ignored
    where it isn't meaningful: a foreign-owned/exotic mount, or a filesystem (Windows/NTFS)
    without POSIX permission bits."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Write the full config (real secrets) back to disk.

    Prefers an atomic temp-file + ``os.replace`` so a crash mid-write can't truncate the
    live config. When the target is a single-file Docker bind mount (``config.yaml`` mapped
    in directly), the rename can't replace the mount point (EBUSY) — and a cross-device tmp
    can't be renamed (EXDEV) — so we fall back to an in-place overwrite. The file is written
    owner-only (0600) so the plaintext secrets aren't world-readable. Raises a clear error
    if the file isn't writable (e.g. mounted read-only).
    """
    p = path or paths.config_path()
    data = cfg.model_dump(mode="python")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        # Create the temp with owner-only perms up front so the plaintext secrets never sit in
        # a world-readable file; os.replace then carries those perms onto config.yaml.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.replace(tmp, p)
        except OSError as exc:
            # Bind-mounted file (EBUSY) or cross-device tmp (EXDEV): can't rename over the
            # target, so overwrite it in place. Not atomic, but it's the only way to persist
            # onto a single-file bind mount, which is how the compose example maps config.yaml.
            if exc.errno not in (errno.EBUSY, errno.EXDEV):
                raise
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            # O_CREAT doesn't change an already-existing file's mode, so tighten it explicitly.
            restrict_secret_file(p)
            tmp.unlink(missing_ok=True)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        raise PermissionError(
            f"Cannot write {p}: {exc}. Ensure config.yaml is mounted writable (not ':ro')."
        ) from exc
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def redact(data: Any) -> Any:
    """Return a deep copy of a config mapping with secret values masked.

    Non-empty secrets become ``***REDACTED***`` so the UI can tell "set" from "unset"
    without exposing the value; empty secrets stay empty.
    """
    data = deepcopy(data)
    return _redact_in_place(data)


def _redact_in_place(node: Any) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SECRET_KEYS:
                node[key] = _mask(value)
            else:
                _redact_in_place(value)
    elif isinstance(node, list):
        for item in node:
            _redact_in_place(item)
    return node


def _mask(value: Any) -> Any:
    if isinstance(value, list):
        return [REDACTED if v else v for v in value]
    return REDACTED if value else value


def redacted_dict(cfg: Config) -> dict[str, Any]:
    """Config as a plain dict with secrets masked — safe to return from the API."""
    return redact(cfg.model_dump(mode="python"))


# --- merge incoming (partially-redacted) config -------------------------------


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto ``base``.

    Dict values are merged key-by-key; every other value — including lists — replaces the
    value in ``base``. Returns a new top-level dict; ``base`` is not mutated at the top level.
    """
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def enforce_server_managed(merged: dict[str, Any], current: Config) -> dict[str, Any]:
    """Force server-owned secrets to their stored values, ignoring whatever the client sent.

    ``app.secret_key`` (session-signing key), ``app.api_key`` (dashboard integration), and
    ``app.auth.password_hash`` (owned solely by PUT /api/account) must never be set or
    cleared through PUT /api/config. Mutates and returns ``merged``.
    """
    app = merged.get("app")
    if isinstance(app, dict):
        app["secret_key"] = current.app.secret_key
        app["api_key"] = current.app.api_key
        auth = app.get("auth")
        if isinstance(auth, dict):
            auth["password_hash"] = current.app.auth.password_hash
    return merged


class RedactionError(ValueError):
    """A redacted secret could not be resolved back to a real value (ambiguous input)."""


def restore_secrets(incoming: dict[str, Any], current: Config) -> dict[str, Any]:
    """Return a copy of ``incoming`` with redacted secrets filled in from ``current``.

    ``GET /api/config`` masks secrets as ``***REDACTED***``; on ``PUT`` the client sends
    that placeholder back for any secret it didn't change. The contract per secret value:
    ``REDACTED`` → keep the stored value; ``""`` → clear it; anything else → set it new.
    """
    merged = deepcopy(incoming)
    _restore_in_place(merged, current.model_dump(mode="python"))
    return merged


def _restore_in_place(node: Any, current: Any) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            cur = current.get(key) if isinstance(current, dict) else None
            if key in SECRET_KEYS:
                node[key] = _unmask(value, cur)
            else:
                _restore_in_place(value, cur)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            cur = current[i] if isinstance(current, list) and i < len(current) else None
            _restore_in_place(item, cur)
    return node


def _unmask(value: Any, current: Any) -> Any:
    if isinstance(value, list):
        # List secrets (custom_urls) are write-only and all-or-nothing to avoid the
        # index-positional corruption of the old per-entry masking:
        #   []                -> clear
        #   all ***REDACTED** -> unchanged: keep the full stored list
        #   all real values   -> replace the whole list
        #   mixed             -> ambiguous (can't map a sentinel to a stored entry) -> reject
        # Load-bearing order: the empty-list check MUST precede the all-sentinel check
        # below — `all(... for v in [])` is vacuously True, so reordering would silently
        # turn "clear" into "keep the stored list". Do not reorder.
        if not value:
            return []
        if all(v == REDACTED for v in value):
            return list(current) if isinstance(current, list) else []
        if any(v == REDACTED for v in value):
            raise RedactionError(
                "custom_urls must be sent in full (all real values) or left unchanged "
                "(all redacted); a mixed list is ambiguous."
            )
        return value
    if value == REDACTED:
        return current if current is not None else ""
    return value
