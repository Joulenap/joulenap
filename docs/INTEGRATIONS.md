# Integrations

Joulenap exposes two read-only, API-key-protected endpoints for other tools:

- **`GET /api/dashboard`** — a flat JSON summary for homelab dashboards
  (Homepage, Homarr, Dashy, Glance). See below.
- **`GET /metrics`** — Prometheus metrics for Grafana. See
  [Prometheus & Grafana](#prometheus--grafana).

Both use the **same API key**, generated once under **Settings →
Integrations**; enabling the integration enables both.

## Enabling it

1. Open Joulenap → **Settings → Integrations**.
2. Click **Generate API key**. The key is shown once — copy it somewhere
   safe (a password manager, your dashboard's secret store, etc.). Joulenap
   only stores a copy needed to verify requests; it won't show you the key
   again.
3. Pick your dashboard in the picker on that page to get a ready-to-paste
   config snippet with the key and endpoint URL already filled in.
4. Disabling the integration (the **Disable** button) clears the key, and
   both endpoints immediately start rejecting requests again.

Regenerating the key invalidates the previous one immediately — update any
dashboard or scrape config that used the old key.

## Authentication

Every request to `/api/dashboard` and `/metrics` must include the API key,
either as:

- an **`X-API-Key` header** (preferred, wherever the client supports custom
  request headers), or
- a **`?key=<your-api-key>` query parameter** appended to the URL, for
  dashboards/widgets that can't set custom headers — and for Prometheus,
  whose `params:` setting works on every version.

No key configured → `403 Forbidden` (integration disabled). Wrong or missing
key → `401 Unauthorized`.

## Dashboard integration

`GET /api/dashboard` lets a homelab dashboard poll Joulenap and show your
backup status alongside your other services: whether the PBS is
asleep/awake/backing up, when the next run is scheduled, how the last run
went, and how full the datastore is.

This endpoint is intentionally separate from the internal `/api/status` used
by Joulenap's own UI: it's a stable, additive-only public contract with
plain machine-readable values (no localization, no session cookie), guarded
by its own API key instead of a login session.

### Response reference

`GET /api/dashboard` returns a flat JSON object:

| Field | Type | Values / meaning |
|-------|------|------------------|
| `pbs_state` | string | `sleeping` \| `online` \| `backing_up` |
| `next_run` | string \| null | ISO 8601 next scheduled backup; null if scheduler disabled |
| `last_run_status` | string | `success` \| `failed` \| `never` |
| `last_run_time` | string \| null | ISO 8601 of the last backup cycle; null if none |
| `datastore_used_pct` | number \| null | Percent used; null if PBS off / probe failed |
| `datastore_used_bytes` | number \| null | Bytes used |
| `datastore_total_bytes` | number \| null | Total bytes (free = total − used) |

### Per-dashboard setup

The endpoint URL is your Joulenap instance's origin plus `/api/dashboard`,
e.g. `http://192.168.1.50:8080/api/dashboard`. Replace `<your-api-key>` with
the key from step 2 above in every snippet below.

#### Homepage

Homepage's built-in `customapi` widget maps JSON response fields directly
onto labelled rows:

```yaml
- Joulenap:
    icon: http://192.168.1.50:8080/assets/joulenap-icon.svg
    href: http://192.168.1.50:8080
    widget:
      type: customapi
      url: http://192.168.1.50:8080/api/dashboard
      headers:
        X-API-Key: <your-api-key>
      mappings:
        - field: pbs_state
          label: PBS
        - field: next_run
          label: Next backup
          format: relativeDate
        - field: last_run_status
          label: Last run
        - field: datastore_used_pct
          label: Datastore
          format: percent
```

#### Glance

Glance's `custom-api` widget fetches the JSON and renders it through a Go
template:

```yaml
- type: custom-api
  title: Joulenap
  url: http://192.168.1.50:8080/api/dashboard
  headers:
    X-API-Key: <your-api-key>
  template: |
    <div>PBS: {{ .JSON.String "pbs_state" }}</div>
    <div>Next: {{ .JSON.String "next_run" }}</div>
    <div>Last run: {{ .JSON.String "last_run_status" }}</div>
    <div>Datastore: {{ .JSON.Int "datastore_used_pct" }}%</div>
```

#### Homarr

> **Note:** Homarr's widget system changed significantly in 2026. Older
> Homarr releases only offered a generic iframe/link-style widget with no
> real JSON field mapping. As of the "Custom Widgets" feature (Homarr
> v1.65+), there is a proper, dashboard-managed **Custom API widget** — no
> YAML file to edit. If you're on an older version, upgrade for the field
> mapping described below; otherwise fall back to an iframe/link widget
> pointed at the `?key=` URL.
>
> Configure it under **Management → Custom Widgets → Add** (or from the
> dashboard's widget picker → *Custom API*, depending on version):
>
> - **URL**: `http://192.168.1.50:8080/api/dashboard`
> - **HTTP Method**: `GET`
> - **Authentication**: `API Key (Header)` → Header Name `X-API-Key`, value
>   `<your-api-key>` (use `API Key (Query)` instead if your Homarr version
>   only offers query-parameter auth, with parameter name `key`)
> - **Display Type**: `Key Value` (or `Custom JSX` for full control over
>   layout)
> - Map the fields you want to show: `pbs_state`, `next_run`,
>   `last_run_status`, `last_run_time`, `datastore_used_pct`,
>   `datastore_used_bytes`, `datastore_total_bytes`
>
> If your version can't set a custom header at all, use the query-string
> fallback for the URL field instead:
> `http://192.168.1.50:8080/api/dashboard?key=<your-api-key>`

#### Dashy

> **Note:** Dashy's generic JSON widget is called `customapi` (it was
> explicitly modeled after Homepage's widget of the same name), not a plain
> iframe. It supports request headers and the same kind of field mappings
> as Homepage:

```yaml
- type: customapi
  options:
    url: http://192.168.1.50:8080/api/dashboard
    headers:
      X-API-Key: <your-api-key>
    mappings:
      - field: pbs_state
        label: PBS
      - field: next_run
        label: Next backup
        format: relativeDate
      - field: last_run_status
        label: Last run
      - field: datastore_used_pct
        label: Datastore
        format: percent
```

If your self-hosted Joulenap doesn't send CORS headers and the widget fails
to fetch, set the widget-level `useProxy: true` so Dashy fetches server-side
instead of from the browser. If your Dashy version predates the `customapi`
widget, use the query-string fallback
(`http://192.168.1.50:8080/api/dashboard?key=<your-api-key>`) with whatever
generic widget your version offers.

## Prometheus & Grafana

`GET /metrics` exposes Joulenap's state in the Prometheus text format, so a
homelab Prometheus can scrape it and Grafana can graph it — and, more to the
point, so Alertmanager can tell you **when a guest stops being backed up**.

It's served at `/metrics` (not under `/api`) because that's Prometheus's
default `metrics_path`.

A scrape reads the database and does the same one-second TCP probe the
dashboard uses. **It never wakes the PBS**, and datastore usage and per-guest
backup times come from Joulenap's cache, so they keep reporting while the
box is asleep — which is most of the time, by design.

### Scrape config

Prometheus's `params:` works on every version, unlike custom scrape headers:

```yaml
scrape_configs:
  - job_name: joulenap
    metrics_path: /metrics
    params:
      key: ["<your-api-key>"]
    static_configs:
      - targets: ["192.168.1.50:8080"]
```

A 60s `scrape_interval` is plenty — nothing here changes faster than a
backup cycle.

### Metric reference

All metrics are gauges prefixed `joulenap_`.

| Metric | Labels | Meaning |
|--------|--------|---------|
| `joulenap_build_info` | `version` | Always 1; the label carries the running version |
| `joulenap_pbs_online` | — | 1 if the PBS answers on its API port, 0 while asleep |
| `joulenap_scheduler_enabled` | — | 1 if the scheduled backup job is armed |
| `joulenap_job_running` | — | 1 while a backup, GC or verify run is in flight |
| `joulenap_next_run_timestamp_seconds` | — | Unix time of the next scheduled backup |
| `joulenap_last_run_timestamp_seconds` | — | When the last finished backup cycle started |
| `joulenap_last_run_success` | — | 1 if the last finished cycle succeeded, else 0 |
| `joulenap_last_run_duration_seconds` | — | How long that cycle took |
| `joulenap_last_run_guests` | — | Guests backed up by that cycle |
| `joulenap_datastore_used_bytes` | — | Datastore bytes used (last known value) |
| `joulenap_datastore_total_bytes` | — | Datastore size in bytes (last known value) |
| `joulenap_guest_last_backup_timestamp_seconds` | `vmid` | Each guest's most recent snapshot |
| `joulenap_pbs_cpu_percent` | — | PBS CPU %, **only present while the PBS is awake** |
| `joulenap_pbs_memory_percent` | — | PBS memory %, only while awake |
| `joulenap_pbs_uptime_seconds` | — | PBS uptime, only while awake |
| `joulenap_runs_recent` | `kind`, `status` | Finished runs in the history window |

Two things worth knowing before you write queries:

- **A value Joulenap doesn't have is an absent series, not a zero.** Before
  the first backup there is no `joulenap_last_run_timestamp_seconds` at all,
  because publishing `0` would graph your last backup as January 1970. Use
  `absent()` to alert on "never ran".
- **`joulenap_runs_recent` is a gauge, not a counter.** The daily prune job
  deletes runs older than `maintenance.history.retention_days`, so the number
  legitimately goes *down* — `rate()` and `increase()` would be nonsense on
  it. It answers "how many failures are in my retention window", not "how
  many ever".

### Useful queries

```promql
# Hours since each guest was last backed up
(time() - joulenap_guest_last_backup_timestamp_seconds) / 3600

# Datastore usage percent
100 * joulenap_datastore_used_bytes / joulenap_datastore_total_bytes

# Days until the datastore is full, from the last week's growth
(joulenap_datastore_total_bytes - joulenap_datastore_used_bytes)
  / (deriv(joulenap_datastore_used_bytes[7d]) * 86400)

# Share of recent backup cycles that succeeded
joulenap_runs_recent{kind="cycle",status="success"}
  / sum by () (joulenap_runs_recent{kind="cycle"})
```

### Alerting rules

The one that justifies wiring this up at all — a guest quietly falling out
of your backup set:

```yaml
groups:
  - name: joulenap
    rules:
      - alert: JoulenapGuestBackupStale
        expr: time() - joulenap_guest_last_backup_timestamp_seconds > 172800
        for: 1h
        annotations:
          summary: "Guest {{ $labels.vmid }} has no backup in over 48h"

      - alert: JoulenapLastBackupFailed
        expr: joulenap_last_run_success == 0
        for: 15m
        annotations:
          summary: "The last Joulenap backup cycle did not succeed"

      - alert: JoulenapNeverRan
        expr: absent(joulenap_last_run_timestamp_seconds)
        for: 24h
        annotations:
          summary: "Joulenap has never completed a backup cycle"

      - alert: JoulenapDatastoreFilling
        expr: 100 * joulenap_datastore_used_bytes / joulenap_datastore_total_bytes > 85
        for: 1h
        annotations:
          summary: "PBS datastore is over 85% full"
```

Set the staleness threshold to comfortably more than your backup interval —
`172800` (48h) suits a nightly schedule; a run that starts late or takes a
while shouldn't page you.
