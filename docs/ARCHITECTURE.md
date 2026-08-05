# Joulenap — Architecture & API


## Goals

- Run scheduled Proxmox VE backups to **backup servers that are normally powered off**: wake → wait → back up → prune → (GC) → power off → notify.
- Support **any number of PVE and PBS boxes**, wired together by explicit **routes**, including PBS→PBS off-site sync.
- Be **config-driven** and distributable (Docker image / LXC), nothing hard-coded.
- Modify **nothing** on the Proxmox host: Joulenap owns its own scheduler and acts via APIs + one SSH command.


## Components

- **Web UI** (frontend): single-page app. Talks to the backend over the REST API below.
- **Backend / API**: serves the UI, exposes the REST API, holds the scheduler, runs the route cycles, manages config.
- **Scheduler**: in-process (APScheduler). **One cron trigger per enabled route**, plus a daily history-prune job (armed independently, so history is trimmed even while every route is paused). Re-armed whenever config changes. GC and verify have no triggers of their own — they are options of a route, or a manual action on a box.
- **Run queue + power lease**: one run is ever in flight; the rest wait in a FIFO queue. Each PBS a run needs is held under a refcounted lease that wakes it on first acquire and powers it off on last release. See below.
- **Connectors**:
  - `pve` — PVE API client (list guests, trigger `vzdump`, read task status).
  - `pbs` — PBS API client (datastore status, GC, verify, remotes and sync jobs). TLS-pinned per device to the fingerprint stored at setup (rejects a changed cert).
  - `wol` — sends the Wake-on-LAN magic packet on the LAN.
  - `power` — SSH to a PBS for `poweroff`, verified against `data/known_hosts` (host key confirmed in the wizard).
  - `notify` — Apprise / Telegram / ntfy / Discord / email senders.
  - `update` — asks GitHub once a day whether a newer release exists (opt-in via `app.update_check`; no outbound call when off).
- **Store**: `config.yaml` for settings; a small SQLite DB (`data/`) for run history and logs.


## The route model

A **route** is one scheduled flow of backup data between devices: *sources → target*, on its own schedule, with its own retention and options. Devices live in `pves[]` and `pbss[]` and are referenced by id; the id is also the name the UI shows.

The **kind** follows from which devices the route names:

| Kind | Sources | Target | What runs |
|---|---|---|---|
| `backup` | one or more PVEs (`sources[].pve`, with a per-source guest selection) | a PBS | `vzdump` on each source, one task per cluster node |
| `sync` | one PBS (`source_pbs`) | another PBS | a PBS remote + sync job, `pull` or `push` |
| `external` | none | a PBS | nothing of its own — it watches the tasks PVE/PBS start on their own schedules |
| `verify` | none | a PBS | a verification pass over the target's snapshots |

Guests are selected **per source** (`sources[].guests`), because vmids collide between PVEs. Mode is `all` or `include`; in `include` mode a newly created guest is *not* picked up automatically.

A route's `schedule` is a time plus seven weekday flags. `schedule.cron` is an escape hatch for anything richer (day-of-month, steps, ranges) and **wins over `time`/`days`** when set; the UI then shows it read-only.

`options` carries the per-route knobs: `mode` / `bwlimit` / `min_free_percent` (backup only, they are vzdump's), `gc` and `verify_after` (run on the target after the data lands), and `reverify_days` for a verify route. `retention` is vzdump's `prune-backups`, per route.

Cross-references are validated at load: ids are unique, every referenced device exists, a **backup** route's target must appear in every source PVE's `storages` map, and an **external** route's target must have `managed_power: true`.


## Queue and power lease

**One run at a time.** A route firing while another run is in flight joins a FIFO queue rather than being dropped; the same route already queued or running is refused (`AlreadyQueued`). The queue key is the route id, or `pbs:<id>:gc` / `pbs:<id>:verify` for an ad-hoc maintenance run.

**Each PBS a run needs is leased.** The lease is refcounted:

- the first holder wakes the box (WoL, then poll until it answers) or finds it already awake — `wol_retries + 1` attempts, each waiting up to `wait_timeout`;
- every holder releases when its run is done, and only the **last** release powers the box off;
- a **sync** route takes two leases (source and target) and releases them independently.

The release records a `poweroff` step whose detail says what actually happened:

| Outcome | Meaning |
|---|---|
| `powered off` | last holder, nothing queued needs it, run succeeded → SSH poweroff |
| `left on: still needed by another run` | a queued run needs this box |
| `left on: Joulenap does not manage this box's power` | `managed_power: false` |
| `left powered on` | the run failed (left up for inspection), the user asked to keep it on, or the box was busy |

Only the last of those is worth a warning; the other three are the correct outcome and are recorded as *skipped*, not failed.

`managed_power: false` describes an always-on PBS. The lease is the single place that knows: acquiring degrades to a reachability check and releasing does nothing.


## What each kind does

- **backup** — `[wake + wait, per lease] → precheck (only when min_free_percent > 0; aborts rather than filling the datastore) → vzdump per source PVE, one task per cluster node, with the route's retention/mode/bwlimit → [GC if enabled] → [verify if enabled] → record → [power-off, per lease]`. A source that fails is recorded and the loop continues; the run ends `failure` naming the sources that broke.
- **sync** — wake both boxes, then on the executing box (`pull` → the target fetches, `push` → the source sends): delete any stale sync job, ensure the remote, ensure the sync job, run it and wait. Then GC/verify on the target. **Order matters**: PBS refuses to delete a remote a job still references, so the job goes first. A task ending in warnings is reported naming the direction, both boxes and the first WARN/ERROR line of its log.
- **external** — wake, then watch: poll up to `external.first_task_wait` for the *first* task to appear, then wait for `external.idle_wait` seconds of continuous silence, restarting that countdown whenever a new task shows up (so a chained backup → prune → GC → sync is not cut short). Joulenap starts nothing itself. A wake where no task ever appears still powers the box back off and says so, so a misfiring PVE/PBS schedule is noticed instead of silently missed.
- **verify** — wake, verify (`reverify_days` keeps it incremental; `0` re-verifies everything), power off.
- **ad-hoc GC / verify on a box** — the homepage's per-PBS buttons. The same steps, so the history reads identically; only the route column is empty.

All steps are recorded in the DB and exposed on the run itself via `/api/runs/{id}` (the activity log — one line per event, not per step — is `/api/logs`); while a run is in progress the raw PVE/PBS task output is tailed into `/api/tasklog` for the UI's task-log panel.


## Upgrading from 0.9 (config migration)

0.9 modelled exactly one PVE, one PBS and one backup job (`pve:` / `pbs:` / `backup:`). On the first start after the upgrade an existing `config.yaml` is converted in place:

- `pve:` → one `pves[]` entry, its single `storage_id` becoming `storages: {<pbs id>: <storage>}`;
- `pbs:` → one `pbss[]` entry, with `managed_power` set from whether a MAC was configured, and 0.9's global external-mode timeouts moved onto the device;
- the backup job → one route named **Backup** (kind `external` if 0.9's external-schedules mode was on, otherwise `backup`), carrying the schedule, guest selection, retention, and `maintenance.gc` / `maintenance.verify.after_backup` as route options;
- a scheduled verification → a second route named **Verify**;
- a plain "at HH:MM on these weekdays" cron becomes `time` + `days`; anything richer is kept verbatim as `schedule.cron`.

Two rules keep it from ever bricking a boot: the original is copied to **`config.yaml.pre-overhaul.bak`** first (an existing `.bak` is never overwritten, and the copy is chmod'd `0600` because it holds every secret), and the converted config is validated before it is adopted — if it fails, the file on disk is left untouched, but nothing in 1.0 reads the 0.9 sections, so the app boots with **no devices and no routes and nothing scheduled**. The reason is surfaced as `config_error` on `/api/status` precisely so the UI says why instead of looking like a fresh install; the user can rewrite `config.yaml` from the Advanced tab while running empty.

One conversion is lossy and deliberately widens rather than narrows: **0.9's `exclude` guest mode no longer exists**. Inverting the list would need a live guest list that is not available at load time, so the route becomes `all` and a warning is logged — a route that backs up more than before, never less. Narrow it from the UI if that is not what you want.


## REST API

Everything is served under `/api`. Auth is a signed **session cookie** started by `/api/login`; every endpoint requires it except `/api/health`, `/api/auth/status`, `/api/auth/setup`, `/api/login` and `/api/logout` (which only clears a cookie, idempotently) — plus `/api/dashboard` and `/metrics`, which are deliberately outside the session and authenticated by the read-only API key instead.

**Health & meta**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | version + liveness (used by the Docker healthcheck) |
| GET | `/api/update` | running version, plus the latest GitHub release when `app.update_check` is on (cached 24h; no outbound call when off) |
| GET | `/api/dashboard` | flat, read-only status for external dashboards — **API-key auth** (`X-API-Key` header or `?key=`), not the session cookie. Payload below; snippets in [`INTEGRATIONS.md`](INTEGRATIONS.md) |
| GET | `/metrics` | Prometheus exposition for Grafana — same API key. The **one route outside `/api`**, because `/metrics` is Prometheus's default `metrics_path` |

**Auth & account**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/auth/status` | whether first-run setup is still needed / already signed in |
| POST | `/api/auth/setup` | first run: create the admin account |
| POST | `/api/login` | authenticate, start session |
| POST | `/api/logout` | end session |
| GET | `/api/auth/me` | current user |
| PUT | `/api/account` | change username / password (requires `current_password`) |

**Status & config**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | the homepage poll: `state`, `scheduler_enabled`, the `running` run, the `queued[]` list, every route's `next_runs[]`, per-device `pves[]` / `pbss[]` (online, lease holders, datastore, load), `last_run`, and `config_error` |
| GET | `/api/config` | current config (secrets redacted) |
| PUT | `/api/config` | validate + save config, re-arm the scheduler |
| GET | `/api/config/yaml` | the redacted config serialised as YAML, for the Advanced tab's editor |
| PUT | `/api/config/yaml` | apply an edited YAML document (same validation and merge as `PUT /api/config`) |
| POST | `/api/config/api-key` | generate/rotate the dashboard-integration API key (returned once) |
| DELETE | `/api/config/api-key` | clear the key, disabling `/api/dashboard` and `/metrics` |
| GET | `/api/guests?pve=` | one PVE's CTs/VMs (id, name, type, node) with each guest's cached last backup and the PBSs holding it |
| POST | `/api/scheduler/toggle` | the global kill-switch across every route; returns the re-armed `next_runs` |

**Routes**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/routes` | every route |
| POST | `/api/routes` | create one (409 on a duplicate id) |
| PUT | `/api/routes/{route_id}` | replace one |
| DELETE | `/api/routes/{route_id}` | delete one. The snapshots on the PBS and the run history survive |
| POST | `/api/routes/{route_id}/run` | run it now — optional `{keep_on}` to leave the boxes awake. 202 with how many runs are ahead of it |

**Devices**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/devices` | `{pves, pbss}`, secrets redacted |
| POST | `/api/devices/{kind}` | create a `pves` / `pbss` entry (409 on a duplicate id) |
| PUT | `/api/devices/{kind}/{device_id}` | update one |
| DELETE | `/api/devices/{kind}/{device_id}` | delete one — **409 naming the routes** still using it |
| POST | `/api/devices/{kind}/{device_id}/test` | live connection test (502 with the reason on failure) |
| POST | `/api/devices/pbss/{pbs_id}/power` | `{action: "wake" \| "poweroff"}` |
| POST | `/api/devices/pbss/{pbs_id}/gc` | queue an ad-hoc GC on this box (optional `{keep_on}`) |
| POST | `/api/devices/pbss/{pbs_id}/verify` | queue an ad-hoc verification on this box |

**Runs, history & logs**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/runs/{run_id}/stop` | stop the run in flight (optional `{power_off}`); also stops the PVE/PBS task behind it. 202 = accepted, not finished — cancellation is cooperative. 409 if it is not the run in flight |
| GET | `/api/runs?limit=&route=` | run history (summaries), optionally filtered to one route |
| GET | `/api/runs/{id}` | one run with its steps + logs |
| GET | `/api/logs?limit=` | recent activity-log lines |
| GET | `/api/tasklog?after=&run=` | PVE/PBS task output — the live tail, or one past run's by id |
| POST | `/api/notify/test` | send a test notification; always 200, with a per-channel outcome |

**Setup wizard** — all stateless: they return discovered values for the frontend to assemble and save with `POST /api/devices/{kind}`. Only `ssh/keygen` and `ssh/trust` write to disk (the shared keypair, and the confirmed host key in `data/known_hosts`).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/wizard/pve/connect` | connect to a PVE, list its nodes + PBS-backed storages (root mode also mints a scoped token) |
| POST | `/api/wizard/storage/derive` | derive PBS host/port/datastore/fingerprint from one storage |
| POST | `/api/wizard/pbs/check` | reach a PBS, read its fingerprint |
| POST | `/api/wizard/pbs/provision` | root mode: auto-create a scoped PBS token |
| POST | `/api/wizard/pbs/grant-sync` | root mode: add the `/remote` roles a sync route needs to an existing token |
| GET | `/api/wizard/interfaces` | local NICs, to pick the WoL broadcast interface |
| POST | `/api/wizard/wol/detect-mac` | detect a PBS MAC via ping + ARP |
| POST | `/api/wizard/wol/test` | send a test magic packet before the device exists |
| POST | `/api/wizard/ssh/keygen` | **get-or-create** the poweroff SSH keypair |
| POST | `/api/wizard/ssh/hostkey` | scan a PBS SSH host key + fingerprint (to confirm before a root password is sent) |
| POST | `/api/wizard/ssh/trust` | persist the user-confirmed host key to `data/known_hosts` |
| POST | `/api/wizard/ssh/install` | root mode: install the public key on the PBS over SSH |


### `GET /api/dashboard` payload

A deliberately separate, additive-only contract for third-party widgets, with machine-style enum values that are never localized.

```json
{
  "state": "idle | running | paused",
  "routes": [
    {
      "id": "nightly", "name": "Nightly", "kind": "backup | sync | external | verify",
      "enabled": true, "next_run": "2026-07-10T02:00:00Z",
      "last_run_status": "success | failed | never", "last_run_time": "2026-07-09T02:00:00Z"
    }
  ],
  "pbss": [
    {
      "id": "pbs-01", "state": "sleeping | online | backing_up",
      "datastore_used_pct": 62,
      "datastore_used_bytes": 1900000000000, "datastore_total_bytes": 3100000000000
    }
  ]
}
```

**Changed in 1.0.0**: the flat single-PBS fields became these two lists, one entry per route and one per PBS. There is no longer a single "next run" or "datastore" to report, so a widget built on 0.9's shape picks a list entry (`.routes[0].next_run`) instead. The same applies to `/metrics`, whose series are now labelled by `route=`, `pbs=` and `datastore=`.


## Permissions cheat-sheet

Per device — each PVE and each PBS gets its own token.

- **PVE token**: `VM.Audit` (list guests) + `VM.Backup` + `Datastore.Audit` + `Datastore.AllocateSpace` **and `Datastore.Allocate`** on the PBS-backed storage (the last is required for vzdump's retention/prune, which deletes old backups). Root-mode setup creates a `Joulenap` role with exactly these privileges (`connectors/provision.py`).
- **PBS token**: `DatastoreAdmin` on the datastore (status, GC, verify) plus `Audit` on `/system` (read-only node CPU/RAM/network for the dashboard). PBS has no API to create custom roles, so root-mode setup grants these built-ins scoped by path.
- **PBS token, additionally, for sync routes**: `RemoteAdmin` **and** `RemoteSyncPushOperator` on `/remote`, so Joulenap can create the remote and the sync job. PBS refuses ACL writes from a token, so these can only be granted from a root login — the wizard does it while it still holds the root ticket, and a box set up before 1.0 gets them from the device editor's **Grant sync permissions** action (`POST /api/wizard/pbs/grant-sync`), which asks for root once and stores nothing. See [`CONFIG-WIZARD.md`](CONFIG-WIZARD.md#sync-routes-need-one-extra-grant).
- **SSH to a PBS**: one dedicated key, shared by every managed box, ideally installed with a forced command that only allows `poweroff`.
