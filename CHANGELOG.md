# Changelog

All notable changes to Joulenap are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — first public release

The first release of Joulenap: schedule energy-saving Proxmox backups to a normally-off Proxmox
Backup Server, all from a web UI.

### Added

- **Backup cycle** — an in-process scheduler runs the full wake → wait → backup → maintenance →
  power-off cycle; nothing on the Proxmox host is modified.
- **Wake-on-LAN** of the PBS with a readiness wait and configurable timeout/retries.
- **vzdump backups** via the PVE API, with per-guest selection (all / include / exclude, plus
  auto-include-new) and snapshot/suspend/stop modes.
- **Retention** (keep last/daily/weekly/monthly/yearly) and **Garbage Collection** after backups.
- **Verify** — optional quick verify after each backup and a scheduled full-verify cycle.
- **SSH power-off** of the PBS (the one action with no API), with a guard that waits for running
  PBS tasks to finish first.
- **Notifications** via Apprise — Telegram, ntfy, email/SMTP, Discord, and custom Apprise/webhook
  URLs — on success and/or failure, localized server-side.
- **Setup wizard** — connect to PVE, derive PBS from the storage config, detect the PBS MAC, and
  optionally auto-provision scoped tokens and the poweroff SSH key. Defaults to no-root token mode;
  root credentials, if given, are used transiently and never stored.
- **Live task-log panel** streaming the real PVE/PBS task output (backup/GC/verify) as it runs.
- **Per-guest last-backup** caching so the dashboard shows dates while the PBS sleeps.
- **Web UI** — dashboard, settings, and login/auth; **i18n** (English + Italian) and a dark/light
  theme; a footer showing the app version.
- **Configurable timezone** (`app.timezone` / `TZ`) so schedules run in your local time, not UTC.
- **Run history + activity log** in SQLite with daily auto-pruning; interrupted runs are cleaned up
  on startup.
- **Packaging** — a multi-stage Docker image (`catubba/joulenap`) with a healthcheck, a
  docker-compose example, and Proxmox LXC support.
- Config-driven via `config.yaml` (pydantic-validated); secrets stay in `config.yaml` and are
  redacted from API responses.

[Unreleased]: https://github.com/Joulenap/joulenap/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Joulenap/joulenap/releases/tag/v0.1.0
