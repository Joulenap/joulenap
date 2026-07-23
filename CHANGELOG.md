# Changelog

All notable changes to Joulenap are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0]

### Added

- **Run history in the interface** — the activity card now has two tabs. Alongside the familiar
  activity log there is a Run history table listing every run with its job type, what triggered it,
  the result, how long it took and how many guests it covered. Clicking a run expands it in place to
  show each step (wake, wait, backup, garbage collection, verify, power-off) with its own duration
  and outcome, plus that run's log lines. Failures show their error without expanding. The history
  is kept for as long as `maintenance.history.retention_days`, which the panel states.
- **Stop a running job** — the Run backup and Run GC buttons turn into a Stop button while a job is
  in progress, with a confirmation dialog that can also power the PBS off once the job has stopped.
  Stopping also cancels the underlying task on the Proxmox side, so a cancelled backup does not keep
  running on the server after Joulenap has stopped watching it. A scheduled verify can be stopped
  the same way. Previously a stuck backup or an unreachable PBS blocked every later job — and manual
  power-off — until the container was restarted.
- **Prometheus metrics for Grafana** — a new `/metrics` endpoint, protected by the same read-only
  API key as the dashboard integration, exposing PBS power state, scheduler state, the last run's
  result and duration, datastore usage, run counts, and the last backup time of every individual
  guest. Scraping never wakes the PBS, and cached values keep reporting while it sleeps. This makes
  it possible to alert on a guest quietly dropping out of your backup set; `docs/INTEGRATIONS.md`
  has the scrape configuration, the full metric list, example queries and ready-made alert rules.

### Changed

- **Notifications name the job that ran.** A scheduled verification or a garbage-collection run that
  failed used to notify "backup failed"; each job type now reports its own outcome, in English and
  Italian.
- **Removed the `backup.guests.auto_include_new` setting.** It never had any effect, while its name
  and default implied newly created guests were picked up automatically. Existing configuration
  files keep working — the key is ignored and dropped on the next save. The behaviour it seemed to
  promise is what "all" and "exclude" mode already do; "include" mode is, and always was, an
  explicit list. The documentation now says so.
- **Documentation accuracy pass.** Corrected the guest-selection and garbage-collection
  descriptions, the Proxmox VE token privilege list (which omitted `Datastore.Audit` and
  `Datastore.Allocate`, so a manually created token would fail at prune time), the Proxmox Backup
  Server token privileges, the supported-versions table, and the API reference, which was missing
  several endpoints. Added a walkthrough of the Settings tabs to the install guide.

### Fixed

- Toggle switches are now announced correctly by screen readers, and can no longer submit a
  surrounding form by accident.
- Repaired three changelog comparison links that pointed at version tags which were never published.

## [0.5.0]

### Added

- **Update check (opt-in, off by default)** — Joulenap can ask GitHub once a day whether a newer
  release exists and show a badge in the footer linking to the release notes. It is disabled
  unless you turn it on in Settings -> Integrations: with it off the app makes no outbound
  internet request at all. The check never runs as part of the container healthcheck.
- **Advanced settings tab** — the settings that previously existed only in `config.yaml` now have a
  home in the interface: backup mode (snapshot / suspend / stop), bandwidth limit, the keep-last and
  keep-yearly retention buckets, how long run history is kept, and the server's port, session
  lifetime and HTTPS-only cookie flag.
- **Edit config.yaml from the browser** — the same tab embeds a YAML editor with syntax
  highlighting for the whole configuration, so anything the forms don't cover is still reachable
  without shelling into the container. Secrets are shown as `***REDACTED***` and are never sent to
  the browser; leaving them untouched keeps the stored value. The document is validated before
  anything is written, a key you delete keeps its current value, and a Copy button gives you a
  secret-free copy of your configuration to attach to a bug report.

## [0.4.4]

### Changed

- **Accessible confirmation dialog** — the dialog shown before every backup, GC, power-off, and
  reset is now fully keyboard- and screen-reader-accessible: it identifies itself as a dialog,
  keeps focus inside while open, closes on Escape, and returns focus to the button that opened it.
- **Self-hosted fonts** — the interface fonts (IBM Plex) are now bundled with the app instead of
  being fetched from Google Fonts, so the UI makes no third-party request on load and renders
  correctly fully offline or air-gapped.
- **Sign-in screens** — the login and first-account screens are now proper forms with correct
  autocomplete hints, so password managers fill and save credentials reliably; the button shows
  progress while signing in.

### Fixed

- **Header status label** — the header now reads "GC running" or "Verify running" during those
  jobs, instead of always saying "Backup running".
- **Selective backup with no guests** — choosing Selective mode with no guests selected is now
  blocked with an explanation, instead of silently saving a schedule that wakes the PBS and backs
  up nothing.
- **Setup wizard error visibility** — an error on a lower wizard step now scrolls into view (and is
  announced to screen readers) instead of appearing off-screen, and "Detect MAC" now tells you when
  auto-detection found nothing instead of doing nothing.
- **Empty guest list** — the guest panel now shows a "No guests found" message when a node has no
  guests, instead of a blank area.

## [0.4.3]

### Added

- **Unsaved-changes guard** — editing a settings tab (Localization, Notifications, Backup safety)
  or the scheduler and then navigating away now asks before discarding the edits, instead of
  losing them silently. Also warns on a browser tab close or refresh while there are unsaved
  changes.
- **Request timeout** — the UI now shows a clear "timed out" message instead of hanging
  indefinitely if the backend stops responding.

### Fixed

- **Setup wizard validation** — a step no longer completes, and Save no longer unlocks, when a
  check fails: an unreachable PBS, a missing PBS API token, or an empty Wake-on-LAN MAC now
  block completion instead of saving a broken configuration.
- **Setup wizard re-save** — no longer reverts a hand-configured Proxmox VE port or TLS setting.
- **Manual actions** — Run backup, Run GC, and Power on/off now show the error when they fail to
  start, instead of appearing to do nothing.
- **Scheduler toggle** — the Enabled switch now explains why it reverted when the change fails,
  instead of silently flipping back.
- **Live task log** — no longer occasionally shows duplicated lines.
- **Guest list** — keeps the last-known guests (with an error note) when a refresh fails, rather
  than blanking to an empty panel. What gets backed up is unaffected: the guest set is resolved
  live at backup time.
- **Localization tab** — no longer shows a "Saved" note before anything was saved, and its
  fields resync if the configuration changes underneath.
- **Integrations copy** — corrected contradictory text about regenerating the API key.
  Regenerating replaces the key and the old one stops working immediately.

## [0.4.2]

### Added

- **Missed-backup alert** — if a scheduled backup was due while Joulenap was down (for example
  the container was stopped over the backup window), it is detected at the next startup and a
  notification is sent. The backup itself is not run automatically — use "Run backup" if you
  want it immediately.
- **Interrupted-run alert** — a run left unfinished by a restart is reported at startup, warning
  you when the PBS was left powered on so you can check on it.
- **Setup prompt on the dashboard** — when Proxmox VE and PBS aren't configured yet (a fresh
  install), the dashboard shows a banner that links straight to the setup wizard.

### Changed

- **Backup notifications now warn when the PBS was left powered on for failed and aborted runs**,
  not only for successful ones — so an energy-costing "still awake" box is never silent.
- **The Scheduler "Apply changes" action now shows saving / saved / error feedback** and can't be
  double-submitted, matching the settings tabs; a failed save surfaces the reason instead of
  doing nothing.

### Fixed

- **Session expiry no longer leaves the UI showing stale data.** When the session expires (or the
  backend restarts), the app returns to the login screen with a notice instead of rendering a
  frozen last-known status indefinitely; a "can't reach Joulenap" banner appears while the backend
  is unreachable and clears on recovery.
- **An invalid Wake-on-LAN MAC address is now rejected when you save the configuration** (with a
  clear error) instead of being accepted and only failing later at backup time.

## [0.4.1]

### Fixed

- **SQLite concurrency** — the database is opened in WAL mode with a busy timeout, so a
  running backup cycle's frequent commits no longer risk "database is locked" errors against
  the dashboard's polling; foreign-key enforcement is also enabled.
- **Manual power-off race** — powering the PBS off manually now holds the single-run lock
  across the operation, so a scheduled cycle can't start in the gap and get its PBS shut down
  mid-backup.
- **Job-lock leak** — if a worker thread fails to start (e.g. resource exhaustion), the run is
  marked failed and the single-run lock is released, instead of being held forever and
  blocking every later run until restart.
- **History-prune timezone** — a timezone change applied at runtime now moves the daily prune
  job into the new zone, instead of leaving it in the boot-time zone until restart.

### Security

- **`config.yaml` written owner-only (0600)** — the config file holds API tokens, the session
  key, and notification secrets, so it is now created with owner-only permissions (matching the
  SSH key) instead of the default world-readable mode.

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

[Unreleased]: https://github.com/Joulenap/joulenap/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/Joulenap/joulenap/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Joulenap/joulenap/compare/v0.4.4...v0.5.0
[0.4.4]: https://github.com/Joulenap/joulenap/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/Joulenap/joulenap/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Joulenap/joulenap/compare/3f94413...v0.4.2
[0.4.1]: https://github.com/Joulenap/joulenap/compare/340646b...3f94413
[0.4.0]: https://github.com/Joulenap/joulenap/compare/v0.3.1...340646b
[0.3.1]: https://github.com/Joulenap/joulenap/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Joulenap/joulenap/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Joulenap/joulenap/compare/v0.1.1...v0.2.0
[0.1.0]: https://github.com/Joulenap/joulenap/releases/tag/v0.1.0
