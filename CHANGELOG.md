# Changelog

All notable changes to Joulenap are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0]

### Added

- **"Keep PBS on" after a manual job** — the Run backup / Run GC confirmation now offers a
  toggle to leave the PBS powered on after the job instead of powering it back off. It defaults
  to the PBS's current state: a box that is already awake (for example, woken for a restore)
  stays on, while one that was asleep goes back to sleep afterwards. `POST /api/backup/run` and
  `POST /api/gc/run` accept an optional `{"keep_on": true}` body. Scheduled runs always power
  off, unchanged.
- **Manual GC now wakes the PBS** — "Run GC" runs as a full wake to GC to power-off cycle, so it
  works against a normally-off PBS instead of failing when the box is asleep.

### Changed

- Manual **Run backup / Run GC** are now available while the PBS is asleep — they wake it
  themselves, so they only require that no other run is already in progress.
- Changing the admin account now requires confirming the current password (`PUT /api/account`),
  so a stolen session alone can no longer rotate the credentials.

### Fixed

- **Setup wizard no longer wipes the PVE token secret on re-save.** Re-saving a completed wizard
  sent an empty secret, which the backend read as "clear it" — silently breaking every
  subsequent backup. The wizard now preserves the stored secret unless a new one is entered.
- **`exclude` guest mode is no longer inverted.** The dashboard showed an `exclude` list as a
  selective (include) set and, on Apply, rewrote it as `include` — flipping the backup set to
  exactly the guests meant to be skipped. Exclude mode is now shown read-only (edit `config.yaml`
  to change it) and preserved on save.
- **An invalid backup cron no longer bricks startup.** An unparseable `backup.schedule` is now
  rejected on save (`422`) and, if already present on disk, is skipped with a warning instead of
  crashing the scheduler on every restart.

## [0.3.1]

### Changed

- **Responsive layout** — the dashboard, header, and settings screen now adapt to narrow
  screens under a single `900px` breakpoint. The settings sidebar collapses into a 2-column
  grid of buttons on a phone. Desktop rendering is unchanged.

### Added

- A dev-only API stub (`frontend/src/devStub.ts`, `npm run dev -- --mode stub`) so
  contributors can work on the UI — including the full setup wizard — against fixture data,
  without a backend or a real Proxmox. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## [0.3.0]

### Added

- **Per-channel notification report** — the "Send test" button now shows one row per channel with
  its own result, instead of a single "Test failed" that could not say which channel broke or why.
  A failing ntfy no longer makes a working Telegram look broken, and the reason reported by the
  delivery engine (unreachable host, `401 Unauthorized`, and so on) is shown next to the channel
  that produced it. Secrets are stripped from the reason before it leaves the backend.
- Failed notifications during a scheduled backup are now logged with the channel name and the
  reason. Previously a channel that quietly stopped working left no trace anywhere.

### Changed

- `POST /api/notify/test` always answers `200` and returns the per-channel report as
  `{"channels": [{"channel", "ok", "error"}, ...]}`. It no longer returns `400` when no channel is
  configured (the report is simply empty) nor `502` when delivery fails — a delivery failure is a
  result, not a transport error. Anything scripting this endpoint and treating a `502` as "the test
  failed" must now read `ok` per channel instead.
- Frontend dependencies moved to React 19, Vite 8, i18next 26 and TypeScript 6.

### Fixed

- A notification sent by the scheduler and a manual test running at the same time could attribute
  one channel's failure reason to another. Each send now captures only its own thread's records.
- An exception raised while parsing a channel's Apprise URL could reach the container logs with the
  URL, and therefore its credentials, unredacted.
- The Docker image builds the web UI on Node 24, matching CI.
- On a transport error the test button no longer reports "couldn't save changes" for an action that
  saved nothing.

## [0.2.0]

### Added

- **Dashboard integration** — a read-only, API-key-protected `GET /api/dashboard` endpoint plus a
  Settings → Integrations panel (generate/rotate/disable the key and copy a ready-made config
  snippet) so Joulenap's status — PBS power state, next/last run, datastore usage — shows on
  homelab dashboards like Homepage, Homarr, Dashy, and Glance. See
  [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md).
- **Persistent datastore usage** — disk used/total is cached whenever the PBS is awake and shown in
  the web UI and the dashboard endpoint even while the PBS is powered off.

### Fixed

- Copy buttons now work over plain HTTP (a non-secure browser context) via a clipboard fallback, so
  the API key and config snippets copy correctly when Joulenap is reached at a LAN `http://` address.

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

[Unreleased]: https://github.com/Joulenap/joulenap/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Joulenap/joulenap/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Joulenap/joulenap/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Joulenap/joulenap/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Joulenap/joulenap/compare/v0.1.1...v0.2.0
[0.1.0]: https://github.com/Joulenap/joulenap/releases/tag/v0.1.0
