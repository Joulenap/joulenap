# Joulenap — Architecture & API


## Goals

- Run scheduled Proxmox VE backups to a **normally-off PBS**: wake → wait → backup → prune → (GC) → power-off → notify.
- Be **config-driven** and distributable (Docker image / LXC), nothing hard-coded.
- Modify **nothing** on the Proxmox host: Joulenap owns its own scheduler and acts via APIs + one SSH command.


## Components

- **Web UI** (frontend): single-page app. Talks to the backend over the REST API below.
- **Backend / API**: serves the UI, exposes the REST API, holds the scheduler, runs the backup cycle, manages config.
- **Scheduler**: in-process (APScheduler). Three jobs: cron-style triggers for the backup job and the scheduled verify, plus a daily history-prune job (armed independently of the backup config, so history is trimmed even while backups are disabled). GC has no trigger of its own — it runs as a step of the backup cycle. Re-armed whenever config changes.
- **Connectors**:
  - `pve` — PVE API client (list guests, trigger `vzdump`, read task status).
  - `pbs` — PBS API client (datastore status, start/poll Garbage Collection, verify). TLS-pinned to the fingerprint stored at setup (rejects a changed cert).
  - `wol` — sends the Wake-on-LAN magic packet on the LAN.
  - `power` — SSH to PBS for `poweroff`, verified against `data/known_hosts` (host key confirmed in the wizard).
  - `notify` — Apprise / Telegram / ntfy / Discord / email senders.
  - `update` — asks GitHub once a day whether a newer release exists (opt-in via `app.update_check`; no outbound call when off).
- **Store**: `config.yaml` for settings; a small SQLite DB (`data/`) for run history and logs.


## Backup cycle (the heart)

1. **Wake**: send WoL to `pbs.mac` on `pbs.wol_broadcast_iface`.
2. **Wait**: poll `pbs.host:pbs.port` until reachable or `wait_timeout` → on timeout, notify + abort.
3. **Backup**: trigger `vzdump` via PVE API for the selected guests, to `pve.storage_id`, with `mode` and `retention` (prune-backups). Poll the task to completion.
4. **Maintenance** (if due): start PBS **GC** via PBS API and **wait** for it to finish; optional verify.
5. **Power-off**: on success, SSH `poweroff` to PBS. On failure, leave it on for inspection.
6. **Notify**: send result (success/failure, duration, guest count, datastore usage) on the enabled channels.

Two sibling cycles reuse the same wake/power-off machinery: a **GC cycle** (wake → GC → power-off, run on demand from the dashboard) and a **verify cycle** (wake → verify → power-off, on its own cron schedule). Either can be asked to leave the PBS awake afterwards — the `{keep_on}` flag on the manual endpoints.

All steps are logged to the DB and exposed via `/api/logs`; while a run is in progress the raw PVE/PBS task output is tailed into `/api/tasklog` for the UI's task-log panel.


## REST API

Everything is served under `/api`. Auth is a signed **session cookie** started by `/api/login`; every endpoint requires it except `/api/health`, `/api/auth/status`, `/api/auth/setup` and `/api/login` — plus `/api/dashboard`, which is deliberately outside the session and authenticated by its own read-only API key instead.

**Health & meta**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | version + liveness (used by the Docker healthcheck) |
| GET | `/api/update` | running version, plus the latest GitHub release when `app.update_check` is on (cached 24h; no outbound call when off) |
| GET | `/api/dashboard` | flat, read-only status for external dashboards — **API-key auth** (`X-API-Key` header or `?key=`), not the session cookie. See [`INTEGRATIONS.md`](INTEGRATIONS.md) |
| GET | `/metrics` | Prometheus exposition for Grafana — same API key. The **one route outside `/api`**, because `/metrics` is Prometheus's default `metrics_path` |

**Auth & account**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/auth/status` | whether first-run setup is still needed / already signed in |
| POST | `/api/auth/setup` | first run: create the admin account |
| POST | `/api/login` | authenticate, start session |
| POST | `/api/logout` | end session |
| GET | `/api/auth/me` | current user |
| PUT | `/api/account` | change username / password |

**Dashboard & config**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | scheduler state, next/last run, PBS power, datastore + node load |
| GET | `/api/config` | current config (secrets redacted) |
| PUT | `/api/config` | validate + save config, re-arm scheduler (the "Apply changes" action) |
| GET | `/api/config/yaml` | the redacted config serialised as YAML, for the Advanced tab's editor |
| PUT | `/api/config/yaml` | apply an edited YAML document (same validation and merge as `PUT /api/config`) |
| POST | `/api/config/api-key` | generate/rotate the dashboard-integration API key (returned once) |
| DELETE | `/api/config/api-key` | clear the key, disabling `/api/dashboard` |
| GET | `/api/guests` | list CTs/VMs from PVE (id, name, type) for the selection panel |
| POST | `/api/scheduler/toggle` | enable/disable the backup job (atomic switch) |

**Jobs & power**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/backup/run` | run a backup cycle now (optional `{keep_on}` to leave the PBS on) |
| POST | `/api/gc/run` | run a GC cycle now: wake → GC → power-off (optional `{keep_on}` to leave the PBS on) |
| POST | `/api/jobs/cancel` | stop the run in flight (`{run_id}`, optional `{power_off}`); also stops the PVE/PBS task behind it. 202 = accepted, not finished — cancellation is cooperative |
| POST | `/api/power/on` | wake the PBS (Wake-on-LAN) |
| POST | `/api/power/off` | power the PBS off (SSH) |
| POST | `/api/wol/test` | send a test magic packet |
| POST | `/api/notify/test` | send a test notification |

**History & logs**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/logs?limit=` | recent activity-log lines |
| GET | `/api/runs?limit=` | run history (summaries) |
| GET | `/api/runs/{id}` | one run with its steps + logs |
| GET | `/api/tasklog?after=` | live PVE/PBS task output for the current run (task-log panel) |

**Setup wizard**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/wizard/pve/connect` | connect to PVE, list nodes + PBS storages (quick mode also mints a scoped token) |
| POST | `/api/wizard/storage/derive` | derive PBS host/port/datastore/fingerprint from a storage |
| POST | `/api/wizard/pbs/check` | reach the PBS, read its fingerprint |
| POST | `/api/wizard/pbs/provision` | quick mode: auto-create a scoped PBS token from root creds |
| GET | `/api/wizard/interfaces` | local NICs, to pick the WoL broadcast interface |
| POST | `/api/wizard/wol/detect-mac` | detect the PBS MAC via ping + ARP |
| POST | `/api/wizard/ssh/keygen` | generate the poweroff SSH keypair |
| POST | `/api/wizard/ssh/hostkey` | scan the PBS SSH host key + fingerprint (to confirm before the root password is sent) |
| POST | `/api/wizard/ssh/trust` | persist the user-confirmed PBS host key to `data/known_hosts` |
| POST | `/api/wizard/ssh/install` | quick mode: install the public key on PBS over root SSH |
| POST | `/api/wizard/reset` | clear the connection config, keep the tuning |

UI convention: text fields are saved with an explicit **Apply changes** (`PUT /api/config`); only the master **enable/disable** toggle applies immediately.

## Permissions cheat-sheet

- **PVE token**: `VM.Audit` (list guests) + `VM.Backup` + `Datastore.Audit` + `Datastore.AllocateSpace` **and `Datastore.Allocate`** on the PBS storage (the last is required for vzdump's retention/prune, which deletes old backups). Quick setup creates a `Joulenap` role with exactly these privileges (`connectors/provision.py`).
- **PBS token**: `DatastoreAdmin` on the datastore (status + start GC) plus `Audit` on `/system` (read-only node CPU/RAM/network for the dashboard). PBS has no API to create custom roles, so quick setup grants these built-ins scoped by path.
- **SSH to PBS**: dedicated key; ideally a forced command on PBS that only allows `poweroff`.
