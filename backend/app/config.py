"""Configuration schema, loader and writer.

The entire app is config-driven: ``config.yaml`` holds every setting and
all secrets. We validate it with pydantic so a malformed file fails clearly at startup,
and we can write it back atomically when the UI applies changes or the wizard saves.
"""

from __future__ import annotations

import errno
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, List, Literal  # noqa: UP035  (List is intentional, see below)

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import config_migrate, paths

log = logging.getLogger(__name__)

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

#: Why the 0.9 -> 1.0 migration was refused on the last :func:`load_config`, or ``None``.
#: A failed migration boots on a config with no devices and no routes, which looks exactly
#: like a fresh install — so the reason has to outlive the log line: ``GET /api/status``
#: reports it to the UI and startup writes it to the activity log. Process-wide because the
#: fact is process-wide; every load clears it and decides again.
MIGRATION_ERROR: str | None = None


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
    # Global scheduler kill-switch (Settings > Advanced). False arms nothing at all — no
    # route ever fires on its schedule — while manual runs still work. Pausing a single
    # route is `routes[].enabled: false`; this is the "stop everything" lever.
    scheduler_enabled: bool = True
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


# --- devices and routes ------------------------------------------------------
#
# The v1.0 model. A **route** is "sources -> target + schedule" and covers every reason
# Joulenap ever wakes a PBS, in four kinds:
#
#   backup    one or more PVE sources -> a PBS target        (vzdump)
#   sync      one PBS source          -> another PBS target  (remote + sync job)
#   external  no source               -> a PBS target        (watch its own scheduled tasks)
#   verify    no source               -> a PBS target        (verify its snapshots)
#
# Devices are listed once under ``pves``/``pbss`` and referenced by id, so two routes onto
# the same PBS share its credentials, its wake settings and its power lease.


class RetentionConfig(_Base):
    """How many snapshots the target datastore keeps — vzdump's prune-backups, per route."""

    keep_last: int = Field(default=0, ge=0)
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    keep_yearly: int = Field(default=0, ge=0)


class PveDevice(_Base):
    """One Proxmox VE endpoint — a standalone node or a cluster (which proxies its nodes).

    There is deliberately no ``node`` field: nodes are discovered at runtime via the API,
    which is also what tells us whether this entry is a cluster. And no display name: the
    id *is* the name shown in the UI.
    """

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    host: str = ""
    port: int = Field(default=8006, ge=1, le=65535)
    verify_tls: bool = False
    api_token_id: str = ""
    api_token_secret: str = ""
    # pbs id -> the storage id THIS pve uses for that PBS (Datacenter > Storage). Each PVE
    # names the same PBS however it likes, so the mapping belongs to the PVE, not the route.
    # Filled in by discovery/the wizard.
    storages: dict[str, str] = Field(default_factory=dict)


class PbsExternalConfig(_Base):
    """Watch timeouts for External routes onto this PBS — how slow *this* box is.

    Both are worst-case timeouts, not fixed delays (see jobs/backup_cycle.watch_external_tasks).
    """

    # Wait at most this many seconds for the first task to appear after wake-up; watching
    # starts as soon as one does. If none appears, the PBS is powered off at expiry.
    first_task_wait: int = Field(default=900, ge=0)
    # Power off only after this many seconds of continuous task silence; the countdown
    # restarts whenever a new task starts.
    idle_wait: int = Field(default=300, ge=0)


class PbsDevice(_Base):
    """One Proxmox Backup Server, with everything needed to wake it and put it back to sleep."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    host: str = ""
    port: int = Field(default=8007, ge=1, le=65535)
    datastore: str = ""
    fingerprint: str = ""
    api_token_id: str = ""
    api_token_secret: str = ""
    # False = an always-on PBS (a VM, or a cloud-hosted one): Joulenap schedules routes
    # against it but never sends WoL and never powers it off, so mac/wol/ssh go unused.
    managed_power: bool = True
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
    external: PbsExternalConfig = Field(default_factory=PbsExternalConfig)

    @model_validator(mode="after")
    def _check_power_fields(self) -> PbsDevice:
        # Only once the device is real (a host is set) — a half-filled entry mid-wizard
        # must still be saveable.
        if self.managed_power and self.host:
            missing = [f for f in ("mac", "ssh_user", "ssh_key_path") if not getattr(self, f)]
            if missing:
                raise ValueError(
                    f"pbs '{self.id}': managed_power is on, so {', '.join(missing)} must be set "
                    "(Joulenap wakes it by MAC and powers it off over SSH). Use "
                    "managed_power: false for an always-on or cloud-hosted PBS."
                )
        return self


class RouteGuests(_Base):
    """Which guests of one source PVE a backup route covers."""

    mode: Literal["all", "include"] = "all"
    # A newly created VM/CT is backed up automatically in "all" mode but NOT in "include"
    # mode — that list is explicit, so add new guests to it yourself.
    # ``typing.List`` (not ``list[int]``) avoids the field name shadowing the builtin
    # during Python 3.14 deferred annotation eval.
    list: List[int] = Field(default_factory=list)  # noqa: UP006


class RouteSource(_Base):
    """One PVE source of a backup route, with its own guest selection.

    Guests are per-source on purpose: vmids collide across PVEs, so a flat list on the
    route could not say which 100 it meant.
    """

    pve: str
    guests: RouteGuests = Field(default_factory=RouteGuests)


class RouteSchedule(_Base):
    """When the route runs: a time of day plus the weekdays it applies to."""

    time: str = Field(default="04:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # Monday..Sunday, exactly 7 flags.
    days: List[bool] = Field(  # noqa: UP006
        default_factory=lambda: [True] * 7, min_length=7, max_length=7
    )
    # Escape hatch for a schedule time+days cannot express — a day-of-month or month
    # pattern, a step value, a weekday range. Migrating a 0.9 cron keeps it here verbatim
    # rather than approximating it. When set it WINS over time/days (which keep their
    # defaults and are ignored), and the UI shows it read-only, like 0.9's advanced-schedule
    # lock. A standard 5-field crontab string.
    cron: str = ""

    @model_validator(mode="after")
    def _check_days(self) -> RouteSchedule:
        if self.cron:
            if len(self.cron.split()) != 5:
                raise ValueError(
                    f"schedule.cron '{self.cron}' is not a 5-field crontab string "
                    "(minute hour day-of-month month day-of-week)."
                )
            return self  # time/days are unused when a raw cron is pinned
        if not any(self.days):
            raise ValueError(
                "schedule.days selects no day, so the route would never run — "
                "set enabled: false to pause a route instead."
            )
        return self


class RouteOptions(_Base):
    """Per-route tuning. The first three apply to backup routes; gc/verify_after to any
    route that leaves the PBS awake (they run before power-off); reverify_days to verify
    routes; the last two to sync routes."""

    mode: Literal["snapshot", "suspend", "stop"] = "snapshot"
    bwlimit: int = Field(default=0, ge=0)  # KiB/s, 0 = unlimited
    # Pre-flight guard: abort before vzdump if the target datastore has less than this
    # percentage free (0 = disabled). Avoids backing up onto a near-full datastore.
    min_free_percent: int = Field(default=0, ge=0, le=100)
    # Garbage-collect the target datastore once the route's work is done.
    gc: bool = True
    # Quick verify of just this run's new snapshots while the PBS is still awake.
    verify_after: bool = False
    # Verify routes only: re-verify snapshots whose last verification is older than this
    # many days, so a verify route stays mostly incremental. 0 = re-verify everything.
    reverify_days: int = Field(default=30, ge=0)
    # Sync routes only. Copy only the newest N snapshots per group (PBS ``transfer-last``);
    # 0 = every snapshot. Snapshots it skips are not "vanished", so remove_vanished leaves
    # them alone on the target.
    transfer_last: int = Field(default=0, ge=0)
    # Sync routes only. PBS ``remove-vanished``: snapshots/groups gone from the source are
    # DELETED on the target as well. Off = the target only ever grows (minus its own
    # retention). Opt-in on purpose: an off-site copy that mirrors deletions is no longer
    # a safety net against a fat-fingered prune on the source.
    remove_vanished: bool = False


class Route(_Base):
    """One scheduled flow of backup data between devices."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = ""
    color: str = Field(default="#f5a524", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True
    # Per-route notification filter; the channels themselves stay global (notifications:).
    notify: bool = True
    # Stored, not inferred: the UI derives the kind from the devices you pick, but config
    # is the source of truth and the cycle branches on it.
    kind: Literal["backup", "sync", "external", "verify"]
    sources: List[RouteSource] = Field(default_factory=list)  # noqa: UP006  (backup only)
    source_pbs: str = ""  # sync only
    target: str  # pbs id
    schedule: RouteSchedule = Field(default_factory=RouteSchedule)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    sync_direction: Literal["pull", "push"] = "pull"
    options: RouteOptions = Field(default_factory=RouteOptions)

    @model_validator(mode="after")
    def _check_kind(self) -> Route:
        # options.mode/bwlimit/min_free_percent are inert on non-backup kinds and are left
        # alone rather than rejected, so the UI's single route form can post one shape.
        if self.kind == "backup":
            if not self.sources:
                raise ValueError(
                    f"route '{self.id}': a backup route needs at least one source pve."
                )
            if self.source_pbs:
                raise ValueError(f"route '{self.id}': source_pbs belongs to sync routes only.")
        elif self.kind == "sync":
            if self.sources:
                raise ValueError(
                    f"route '{self.id}': a sync route has no pve sources — its source is "
                    "another PBS, set via source_pbs."
                )
            if not self.source_pbs:
                raise ValueError(f"route '{self.id}': a sync route needs source_pbs.")
            if self.source_pbs == self.target:
                raise ValueError(
                    f"route '{self.id}': source_pbs and target are the same pbs "
                    f"('{self.target}') — a sync route needs two different ones."
                )
        else:  # external | verify
            if self.sources or self.source_pbs:
                raise ValueError(
                    f"route '{self.id}': a {self.kind} route takes no sources — it only "
                    "wakes its target and works there."
                )
        return self


# --- maintenance -------------------------------------------------------------


class HistoryConfig(_Base):
    # Auto-prune run history + activity-log rows older than this many days so the
    # SQLite DB under data/ can't grow without bound on a small disk. The prune runs
    # daily (see core/scheduler.py). 0 = keep everything forever (no pruning).
    retention_days: int = Field(default=14, ge=0)


class MaintenanceConfig(_Base):
    # GC and verify are per-route options now (``routes[].options.gc`` / ``.verify_after``,
    # and the Verify route kind); history retention has no route equivalent and stays here.
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
    # v1.0 route model. Empty on a fresh install: the wizard adds devices, the user adds
    # routes. Field order here is the key order save_config() writes.
    pves: List[PveDevice] = Field(default_factory=list)  # noqa: UP006
    pbss: List[PbsDevice] = Field(default_factory=list)  # noqa: UP006
    routes: List[Route] = Field(default_factory=list)  # noqa: UP006
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @model_validator(mode="after")
    def _check_references(self) -> Config:
        """Cross-check the route graph: ids are unique, references resolve, and a backup
        route's target is actually reachable from every one of its sources.

        Nothing fires while the three lists are empty, which is the fresh-install state.
        """
        _require_unique_ids("pves", [d.id for d in self.pves])
        _require_unique_ids("pbss", [d.id for d in self.pbss])
        _require_unique_ids("routes", [r.id for r in self.routes])

        pves = {d.id: d for d in self.pves}
        pbss = {d.id: d for d in self.pbss}
        for route in self.routes:
            target = pbss.get(route.target)
            if target is None:
                raise ValueError(_unknown(route, "target", route.target, "pbs", pbss))
            if route.source_pbs and route.source_pbs not in pbss:
                raise ValueError(_unknown(route, "source_pbs", route.source_pbs, "pbs", pbss))
            for source in route.sources:
                pve = pves.get(source.pve)
                if pve is None:
                    raise ValueError(_unknown(route, "source", source.pve, "pve", pves))
                if route.kind == "backup" and route.target not in pve.storages:
                    raise ValueError(
                        f"route '{route.id}': pve '{source.pve}' has no storage mapping for "
                        f"pbs '{route.target}' — add it under pves[{source.pve}].storages as "
                        "the storage name that PVE uses for that PBS (Datacenter > Storage)."
                    )
            if route.kind == "external" and not target.managed_power:
                raise ValueError(
                    f"route '{route.id}': an External route watches a PBS Joulenap wakes, but "
                    f"pbs '{route.target}' has managed_power: false — there is nothing to wake "
                    "or power off, and PVE/PBS already own those schedules."
                )
        return self


def _require_unique_ids(section: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for value in ids:
        if value in seen:
            raise ValueError(f"{section}: duplicate id '{value}' — ids must be unique.")
        seen.add(value)


def _unknown(route: Route, field: str, value: str, kind: str, known: dict[str, Any]) -> str:
    names = ", ".join(sorted(known)) or "none configured"
    return f"route '{route.id}': {field} '{value}' is not a known {kind} id (have: {names})."


# --- load / save / redact ----------------------------------------------------


def _drop_legacy_keys(raw: dict[str, Any]) -> None:
    """Strip keys removed in a later version, in place, before validation.

    ``_Base`` forbids extra keys, so a field we delete from the schema would make every
    existing ``config.yaml`` fail at startup — the app wouldn't boot after a container pull
    (the BE-B1 lesson: never brick the app on load). Dropping the key here keeps the old file
    valid; the next save rewrites it without the key.

    Removed in 0.6.0: ``backup.guests.auto_include_new`` — it never had any effect. Include
    mode is an explicit list; ``all`` and ``exclude`` already cover newly created guests.

    Removed in 1.0.0: the whole single-PVE/single-PBS model — ``pve:``/``pbs:``/``backup:``
    and ``maintenance.gc``/``maintenance.verify``, replaced by ``pves``/``pbss``/``routes``
    and the per-route options. ``config_migrate`` reads those keys to build the routes, so
    this must run *after* the migration, never before it (see ``load_config``).
    """
    for key in ("pve", "pbs", "backup"):
        raw.pop(key, None)
    maintenance = raw.get("maintenance")
    if isinstance(maintenance, dict):
        maintenance.pop("gc", None)
        maintenance.pop("verify", None)


def load_config(path: Path | None = None) -> Config:
    """Read and validate ``config.yaml``. Raises with a clear message if missing/invalid."""
    p = path or paths.config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"Config file not found at {p}. Copy config.example.yaml to config.yaml."
        )
    global MIGRATION_ERROR
    MIGRATION_ERROR = None  # re-decided below; a reload after a fix must clear it
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config at {p} must be a YAML mapping, got {type(raw).__name__}.")
    # Migration first: it *reads* the 0.9 sections that _drop_legacy_keys removes, and
    # needs_migration keys on their presence. Dropping first would silently skip every
    # 0.9 migration and hand the user an empty config.
    if config_migrate.needs_migration(raw):
        migrated = _migrate_0_9(raw, p)
        if migrated is not None:
            return migrated
    _drop_legacy_keys(raw)
    return Config.model_validate(raw)


def _migrate_0_9(raw: dict[str, Any], path: Path) -> Config | None:
    """Convert a 0.9 config to the route model, back it up and save it.

    Returns ``None`` — meaning "carry on with the config as it is on disk" — if anything at
    all goes wrong. A failed migration must never stop the app from booting (BE-B1).

    But it is not a harmless deferral any more: in 0.9 the app kept running on the old
    sections, while in 1.0 nothing reads them, so the fallback boots a config with no
    devices and no routes — indistinguishable from a fresh install, with every schedule
    silently gone. Hence :data:`MIGRATION_ERROR` (surfaced by ``GET /api/status`` and the
    activity log) and the ``.bak``: the user can still rewrite ``config.yaml`` from the
    Advanced tab while running empty, and without the copy that would destroy the only
    0.9 config they have.
    """
    try:
        cfg = Config.model_validate(config_migrate.migrate(raw))
    except Exception as exc:  # noqa: BLE001 — any failure here degrades to "don't migrate"
        global MIGRATION_ERROR
        MIGRATION_ERROR = (
            f"Could not convert {path} to the 1.0 route model: {exc}. Joulenap started "
            "with no devices and no routes — nothing is scheduled and no backup will run "
            "until this is fixed. The file is untouched; a copy is at "
            f"{path.name}{config_migrate.BACKUP_SUFFIX}."
        )
        log.error("config: %s", MIGRATION_ERROR)
        config_migrate.write_backup(path)
        return None
    config_migrate.write_backup(path)
    try:
        save_config(cfg, path)
        log.info("config: migrated %s to the 1.0 route model (%d route(s))", path, len(cfg.routes))
    except OSError as exc:
        log.warning(
            "config: migrated config could not be written to %s (%s); running with it in "
            "memory and retrying on the next start",
            path,
            exc,
        )
    return cfg


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
    return restore_secrets_from(incoming, current.model_dump(mode="python"))


def restore_secrets_from(incoming: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """:func:`restore_secrets` against any stored mapping, not just a whole ``Config``.

    The device endpoints edit one device at a time and resolve its placeholders against that
    device's own stored values, which is what makes reordering the device list harmless.
    """
    merged = deepcopy(incoming)
    _restore_in_place(merged, stored)
    return merged


def _restore_in_place(node: Any, current: Any) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            cur = current.get(key) if isinstance(current, dict) else None
            if key in SECRET_KEYS:
                node[key] = _unmask(value, cur, key)
            else:
                _restore_in_place(value, cur)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            matched = _match(item, current, i)
            _restore_in_place(item, matched)
            _keep_omitted_secrets(item, matched)
    return node


def _keep_omitted_secrets(item: Any, current: Any) -> None:
    """Carry a stored secret over to ``item`` when the client left the field out entirely.

    ``deep_merge`` merges mappings key-by-key but **replaces lists wholesale**, so a key
    omitted under ``notifications.telegram`` keeps its stored value while the same omission
    inside ``pves``/``pbss`` simply vanishes — and ``_unmask`` never sees it, because that
    guard only fires on a ``***REDACTED***`` that is *present*. In 0.9 devices lived under
    mappings and were protected by that asymmetry; 1.0 moved them into lists and lost it.

    Deleting the ``api_token_secret:`` line in the Advanced YAML editor therefore returned
    200 and wiped the token, which also contradicted ``api/config.py``'s documented promise
    that "a key the user deletes keeps its stored value rather than being wiped".

    Only ever *adds* a key that is absent: an explicit ``""`` still means "clear it", which
    is the documented way to remove a credential.
    """
    if not (isinstance(item, dict) and isinstance(current, dict)):
        return
    for key in SECRET_KEYS:
        if key not in item and key in current:
            item[key] = deepcopy(current[key])


def _match(item: Any, current: Any, index: int) -> Any:
    """Find ``item``'s counterpart in the stored list.

    By ``id`` whenever both sides carry one — ``pves``/``pbss``/``routes`` are keyed lists,
    and matching them positionally maps a ``***REDACTED***`` placeholder onto the *wrong*
    device's stored secret as soon as the client reorders or shortens the list. Index is
    the fallback for genuinely positional lists.
    """
    if not isinstance(current, list):
        return None
    if isinstance(item, dict) and "id" in item:
        return next(
            (c for c in current if isinstance(c, dict) and c.get("id") == item["id"]), None
        )
    return current[index] if index < len(current) else None


def _unmask(value: Any, current: Any, key: str = "") -> Any:
    """Resolve one secret field: ``REDACTED`` -> the stored value, anything else verbatim.

    An unresolvable placeholder is an **error**, never an empty string. ``current`` is None
    when the incoming item has no stored counterpart — a device whose ``id`` was renamed in
    the YAML editor, or one being created — and the client is then echoing back a mask over
    a secret we do not have. Turning that into ``""`` validates fine, returns 200, and
    leaves the credential gone with nothing on screen to suggest it.
    """
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
            if not isinstance(current, list):
                raise _unresolvable(key)
            return list(current)
        if any(v == REDACTED for v in value):
            raise RedactionError(
                "custom_urls must be sent in full (all real values) or left unchanged "
                "(all redacted); a mixed list is ambiguous."
            )
        return value
    if value == REDACTED:
        if current is None:
            raise _unresolvable(key)
        return current
    return value


def _unresolvable(key: str) -> RedactionError:
    field = f"'{key}'" if key else "a secret"
    return RedactionError(
        f"{field} was sent as {REDACTED} but there is no stored value to restore it from. "
        "This happens when an entry's id is renamed (the old entry's secrets don't follow "
        "the new id) or when a new one is created from a copy. Send the real value, or an "
        "empty string to clear it."
    )
